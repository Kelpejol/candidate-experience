"""Batch robustness for the helpdesk pipeline.

process_ticket can already have written a draft or tags to a REAL Zoho ticket
by the time something later fails, so a batch-wide rollback would leave our
audit log denying writes that actually happened — and one bad ticket must never
stop the rest of the queue.
"""

from datetime import datetime

from sqlmodel import select

from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services import helpdesk_ai_service
from app.services.helpdesk_ai_service import process_pending_tickets
from app.services.helpdesk_thread_context import ThreadContext


def _mirror(ticket_id):
    return HelpdeskTicketMirror(
        zoho_ticket_id=ticket_id,
        channel="Email",
        zoho_status="Open",
        last_synced_at=datetime.utcnow(),
    )


def test_one_failing_ticket_does_not_discard_the_others(session, monkeypatch):
    for tid in ("t1", "t2", "t3"):
        session.add(_mirror(tid))
    session.commit()

    def fake_process(sess, client, mirror):
        if mirror.zoho_ticket_id == "t2":
            raise RuntimeError("LLM gateway timed out")
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type="draft_reply",
            rule="answerable_draft_first",
        )
        sess.add(action)
        return action

    monkeypatch.setattr(helpdesk_ai_service, "process_ticket", fake_process)

    tally = process_pending_tickets(session, zoho_client=object())

    # The two healthy tickets are processed and persisted; the failure is counted.
    assert tally["draft_reply"] == 2
    assert tally["error"] == 1

    stored = {
        a.zoho_ticket_id
        for a in session.exec(select(HelpdeskAIAction)).all()
    }
    assert stored == {"t1", "t3"}


def test_failure_rolls_back_only_the_failing_ticket(session, monkeypatch):
    session.add(_mirror("t1"))
    session.commit()

    def fake_process(sess, client, mirror):
        # Stage a row, then fail — the staged row must not survive.
        sess.add(HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type="draft_reply", rule="partial",
        ))
        raise RuntimeError("Zoho rejected the write")

    monkeypatch.setattr(helpdesk_ai_service, "process_ticket", fake_process)

    tally = process_pending_tickets(session, zoho_client=object())

    assert tally == {"error": 1}
    assert session.exec(select(HelpdeskAIAction)).all() == []


class _FakeTicketLock:
    def __init__(self, locked_ids=None):
        self.locked_ids = set(locked_ids or [])
        self.released = []

    def acquire(self, zoho_ticket_id):
        if zoho_ticket_id in self.locked_ids:
            return None
        return f"token-{zoho_ticket_id}"

    def release(self, zoho_ticket_id, token):
        self.released.append((zoho_ticket_id, token))


def test_locked_ticket_is_skipped_without_processing(session, monkeypatch):
    for tid in ("t1", "t2"):
        session.add(_mirror(tid))
    session.commit()

    processed = []

    def fake_process(sess, client, mirror):
        processed.append(mirror.zoho_ticket_id)
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type="draft_reply",
            rule="answerable_draft_first",
        )
        sess.add(action)
        return action

    monkeypatch.setattr(helpdesk_ai_service, "process_ticket", fake_process)
    lock = _FakeTicketLock(locked_ids={"t1"})

    tally = process_pending_tickets(session, zoho_client=object(), ticket_lock=lock)

    assert tally["skipped_locked"] == 1
    assert tally["draft_reply"] == 1
    assert processed == ["t2"]
    assert lock.released == [("t2", "token-t2")]


def test_draft_gateway_failure_escalates_with_an_honest_reason(session, monkeypatch):
    """A gateway outage must route to a human — and must not be recorded as
    'the KB was insufficient', which officers triage completely differently."""
    mirror = _mirror("t1")
    mirror.candidate_name = "Ada Bello"
    mirror.subject = "Cannot start my test"
    session.add(mirror)
    session.commit()

    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="I cannot start my test",
            latest_candidate_text="I cannot start my test",
            has_readable_candidate_content=True,
            has_attachments=False,
            attachment_notes=[],
            selected_thread_ids=["thread-1"],
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "classify_ticket",
        lambda subject, body: _classification(),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "decide_ticket_action",
        lambda classification, **kwargs: helpdesk_ai_service.TicketDecision(
            action="draft_reply", rule="answerable_draft_first", reason="",
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "retrieve_grounding",
        lambda q, tool_scope=None, campaign_scope=None: helpdesk_ai_service.GroundingResult(
            grounded=True, best_distance=0.2, chunks=["some kb text"],
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "apply_grounding_gate",
        lambda decision, grounding, confidence_label=None: (decision, "grounded"),
    )
    # The gateway is down.
    def boom(**kwargs):
        raise RuntimeError("gateway 503")
    monkeypatch.setattr(helpdesk_ai_service, "generate_draft_reply", boom)
    monkeypatch.setattr(
        helpdesk_ai_service, "execute_ticket_action", lambda *a, **k: False
    )

    action = helpdesk_ai_service.process_ticket(session, object(), mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "draft_writer_unavailable"
    assert "gateway" in (action.reason or "").lower()
    assert action.draft_text is None


def test_unparseable_classification_routes_to_a_human_instead_of_looping_forever(
    session, monkeypatch
):
    """Left uncaught, a ticket the classifier can't parse gets no
    HelpdeskAIAction row, so find_pending_tickets re-selects it every tick
    forever with no human ever alerted."""
    mirror = _mirror("t1")
    session.add(mirror)
    session.commit()

    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="some message",
            latest_candidate_text="some message",
            has_readable_candidate_content=True,
            has_attachments=False,
            attachment_notes=[],
            selected_thread_ids=["thread-1"],
        ),
    )

    def boom(**kwargs):
        raise ValueError("model returned prose, not JSON")
    monkeypatch.setattr(helpdesk_ai_service, "classify_ticket", boom)
    monkeypatch.setattr(
        helpdesk_ai_service, "execute_ticket_action", lambda *a, **k: False
    )

    action = helpdesk_ai_service.process_ticket(session, object(), mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "classifier_unparseable"

    # And the ticket is no longer "pending" — it won't loop forever.
    from app.services.helpdesk_ai_service import find_pending_tickets
    session.commit()
    assert find_pending_tickets(session) == []


def test_empty_or_unreadable_thread_routes_to_human_before_classification(
    session, monkeypatch
):
    mirror = _mirror("t1")
    session.add(mirror)
    session.commit()

    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="[Attachment: screenshot.png] OCR not configured",
            latest_candidate_text="",
            has_readable_candidate_content=False,
            has_attachments=True,
            attachment_notes=["[Attachment: screenshot.png] OCR not configured"],
            selected_thread_ids=["thread-1"],
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service,
        "classify_ticket",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not classify")),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "execute_ticket_action", lambda *a, **k: False
    )

    action = helpdesk_ai_service.process_ticket(session, object(), mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "empty_or_unreadable_candidate_message"


class _classification:
    """Minimal stand-in for a ClassificationResult."""
    issue_category = "technical_issue"
    tool_name = None
    campaign_name = None
    confidence_label = "high"
    sensitivity_detected = False
