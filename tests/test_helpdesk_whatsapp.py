"""WhatsApp channel-aware behavior: no draft step (real-time, conversational),
a grounded answer sends directly as "auto_reply" instead, and a candidate
explicitly asking for a person always escalates — all gated by its own
(more cautious, off-by-default) execute flag, separate from email's."""

from datetime import datetime

import pytest

from app.core.config import get_settings
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services import helpdesk_ai_service
from app.services.helpdesk_ai_service import is_conversational_channel, process_ticket
from app.services.helpdesk_decision import detect_human_request
from app.services.helpdesk_kb_service import GroundingResult
from app.services.helpdesk_thread_context import ThreadContext


def _mirror(channel="WhatsApp", **overrides):
    defaults = dict(
        zoho_ticket_id="t1",
        channel=channel,
        zoho_status="Open",
        subject="Test question",
        candidate_name="Ada Bello",
        last_synced_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    return HelpdeskTicketMirror(**defaults)


class _Classification:
    issue_category = "technical_issue"
    tool_name = None
    campaign_name = None
    confidence_label = "high"
    sensitivity_detected = False


class _FakeZohoClient:
    def __init__(self):
        self.whatsapp_sent = []
        self.drafts_created = []

    def send_whatsapp_reply(self, ticket_id, content):
        self.whatsapp_sent.append((ticket_id, content))

    def create_draft_reply(self, **kwargs):
        self.drafts_created.append(kwargs)


def _patch_common(monkeypatch, *, grounded=True, chunks=None):
    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="I can't get my test page to load",
            latest_candidate_text="I can't get my test page to load",
            has_readable_candidate_content=True,
            has_attachments=False,
            attachment_notes=[],
            selected_thread_ids=["thread-1"],
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "classify_ticket", lambda subject, body: _Classification(),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "retrieve_grounding",
        lambda q, tool_scope=None, campaign_scope=None: GroundingResult(
            grounded=grounded,
            best_distance=0.2 if grounded else 0.9,
            chunks=chunks if chunks is not None else (
                [{"text": "Try a different browser."}] if grounded else []
            ),
        ),
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "execute_ticket_action", lambda *a, **k: False,
    )


# --- detect_human_request (pure function) ----------------------------------

@pytest.mark.parametrize("text", [
    "Can I speak to a human please?",
    "I want to talk to someone",
    "connect me with a human",
    "I NEED A REAL PERSON",
])
def test_detect_human_request_matches(text):
    assert detect_human_request(text) is not None


def test_detect_human_request_no_match_on_ordinary_question():
    assert detect_human_request("My test page won't load, please help") is None


# --- channel normalization ---------------------------------------------------

@pytest.mark.parametrize("channel", ["WhatsApp", "whatsapp", "WhatsApp Business", "Chat", "IM"])
def test_conversational_channel_variants_are_normalized(channel):
    assert is_conversational_channel(channel) is True


@pytest.mark.parametrize("channel", ["Email", "Phone", None, ""])
def test_non_conversational_channels_do_not_use_whatsapp_path(channel):
    assert is_conversational_channel(channel) is False


# --- process_ticket: WhatsApp branch -----------------------------------------

def test_whatsapp_candidate_asking_for_human_escalates_before_generation(
    session, monkeypatch
):
    mirror = _mirror(subject="", )
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch)
    # If generation were reached, this would raise — proves the human-request
    # backstop short-circuits before any reply is even attempted.
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not generate")),
    )
    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="please connect me to a human agent",
            latest_candidate_text="please connect me to a human agent",
            has_readable_candidate_content=True,
            has_attachments=False,
            attachment_notes=[],
            selected_thread_ids=["thread-1"],
        ),
    )

    action = process_ticket(session, _FakeZohoClient(), mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "candidate_requested_human"


def test_whatsapp_grounded_answer_sends_directly_when_flag_on(
    session, monkeypatch
):
    monkeypatch.setenv("HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: "Try refreshing the page or a different browser.",
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert action.executed is True
    assert zoho_client.whatsapp_sent == [
        ("t1", "Try refreshing the page or a different browser.")
    ]
    # The email path must never be touched for a WhatsApp ticket.
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_whatsapp_grounded_answer_not_sent_when_flag_off(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE", "false")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: "Try a different browser.",
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    # Still labeled and recorded as auto_reply (what the AI decided), but
    # nothing was actually sent — same "record, don't act" safety as drafts.
    assert action.action_type == "auto_reply"
    assert action.executed is False
    assert action.draft_text == "Try a different browser."
    assert zoho_client.whatsapp_sent == []

    get_settings.cache_clear()


def test_chat_channel_uses_conversational_reply_path(session, monkeypatch):
    """Zoho can surface social/WhatsApp-style conversations as Chat; those
    must not fall into the formal email draft path."""
    monkeypatch.setenv("HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE", "false")
    get_settings.cache_clear()

    mirror = _mirror(channel="Chat")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: "Try a different browser.",
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_draft_reply",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not use email generator")),
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert action.draft_text == "Try a different browser."
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_whatsapp_ungrounded_question_escalates(session, monkeypatch):
    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=False)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert zoho_client.whatsapp_sent == []


def test_whatsapp_escalation_reasons_still_apply(session, monkeypatch):
    """Sensitive/complaint/low-confidence backstops are shared with email —
    a WhatsApp ticket must escalate on them exactly the same way."""
    mirror = _mirror(subject="I will sue you over this")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch)
    monkeypatch.setattr(
        helpdesk_ai_service,
        "build_thread_context",
        lambda client, tid, conversational=False: ThreadContext(
            text="not relevant",
            latest_candidate_text="not relevant",
            has_readable_candidate_content=True,
            has_attachments=False,
            attachment_notes=[],
            selected_thread_ids=["thread-1"],
        ),
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "complaint_keyword_backstop"
    assert zoho_client.whatsapp_sent == []


def test_email_channel_is_completely_unaffected(session, monkeypatch):
    """A plain Email ticket must still use the draft path, never the
    WhatsApp one — regression guard for the channel branch."""
    mirror = _mirror(channel="Email", candidate_email="ada@example.com")
    session.add(mirror)
    session.commit()

    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    get_settings.cache_clear()

    _patch_common(monkeypatch, grounded=True)
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_draft_reply", lambda **k: "Here is the answer.",
    )
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not use WhatsApp generator")),
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "draft_reply"
    assert len(zoho_client.drafts_created) == 1
    assert zoho_client.whatsapp_sent == []

    get_settings.cache_clear()
