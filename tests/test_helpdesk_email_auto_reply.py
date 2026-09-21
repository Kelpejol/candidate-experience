"""Email full-auto-reply: helpdesk_email_auto_reply_execute skips the draft
step and sends immediately via ZohoDeskClient.send_reply — a strictly bigger
trust step than the existing draft flag, so it's a separate, off-by-default
setting that takes priority over helpdesk_draft_execute when both are set."""

from datetime import datetime

import pytest

from app.core.config import get_settings
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services import helpdesk_ai_service
from app.services.helpdesk_ai_service import has_valid_email_recipient, process_ticket
from app.services.helpdesk_kb_service import GroundingResult
from app.services.helpdesk_thread_context import ThreadContext


@pytest.fixture(autouse=True)
def _known_flag_baseline(monkeypatch):
    """Force every helpdesk execute-flag this file cares about to a known,
    off baseline, regardless of what the real deployed environment's .env
    happens to have live (found 2026-09-17: the deployed VM's real .env has
    HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS and HELPDESK_EMAIL_AUTO_REPLY_EXECUTE
    both set from real production testing, which silently broke every test
    that assumed a flag defaults to off — first just the allowlist, then,
    once that was fixed, the execute flag itself).

    Deliberately setenv(..., "") / setenv(..., "false") rather than delenv:
    pydantic-settings reads straight from the .env FILE (env_file=".env" in
    config.py), so deleting an os.environ var that was never set there in
    the first place is a no-op — the dotenv-sourced value still wins. An
    explicit env var, even an empty/false one, is what actually overrides a
    dotenv value; confirmed live on the deployed VM after a delenv-based
    version of this fixture silently failed to fix anything there, despite
    passing locally where no such .env lines exist at all.

    Tests that specifically exercise a flag set it themselves after this
    fixture runs, which overrides it as intended."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS", "")
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "false")
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "false")
    monkeypatch.setenv("HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _mirror(**overrides):
    defaults = dict(
        zoho_ticket_id="t1",
        channel="Email",
        zoho_status="Open",
        subject="Test question",
        candidate_name="Ada Bello",
        candidate_email="ada@example.com",
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
        self.sent = []
        self.drafts_created = []

    def send_reply(self, **kwargs):
        self.sent.append(kwargs)

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
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_draft_reply",
        lambda **k: "Try refreshing the page or a different browser.",
    )


@pytest.mark.parametrize("email", ["ada@example.com", " ada@example.com "])
def test_valid_email_recipient_detection(email):
    assert has_valid_email_recipient(email) is True


@pytest.mark.parametrize("email", [None, "", "not-an-email", "ada@", "@example.com"])
def test_invalid_email_recipient_detection(email):
    assert has_valid_email_recipient(email) is False


def test_grounded_answer_sends_directly_when_flag_on(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert action.executed is True
    assert len(zoho_client.sent) == 1
    assert zoho_client.sent[0]["ticket_id"] == "t1"
    assert zoho_client.sent[0]["content"] == "Try refreshing the page or a different browser."
    assert zoho_client.sent[0]["to"] == "ada@example.com"
    # The draft path must never fire once auto-send is on.
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_grounded_answer_not_sent_when_flag_off(session, monkeypatch):
    """Default (off) behavior: still recorded, nothing touches Zoho."""
    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "draft_reply"
    assert action.executed is False
    assert action.draft_text == "Try refreshing the page or a different browser."
    assert zoho_client.sent == []
    assert zoho_client.drafts_created == []


def test_email_candidate_asking_for_human_escalates_before_grounding(
    session, monkeypatch
):
    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
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
    monkeypatch.setattr(
        helpdesk_ai_service,
        "retrieve_grounding",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not retrieve")),
    )
    monkeypatch.setattr(
        helpdesk_ai_service,
        "generate_draft_reply",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not draft")),
    )
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "candidate_requested_human"
    assert zoho_client.sent == []
    assert zoho_client.drafts_created == []


def test_draft_execute_still_works_when_auto_reply_flag_is_off(session, monkeypatch):
    """Regression guard: adding auto-send must not break the existing,
    already-verified-live draft path when only that flag is on."""
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "draft_reply"
    assert action.executed is True
    assert len(zoho_client.drafts_created) == 1
    assert zoho_client.sent == []

    get_settings.cache_clear()


def test_auto_reply_takes_priority_when_both_flags_are_on(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert len(zoho_client.sent) == 1
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_auto_reply_test_allowlist_sends_for_listed_address(session, monkeypatch):
    """A candidate email on the allowlist gets the real auto-send path."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS", "ada@example.com, other@example.com")
    get_settings.cache_clear()

    mirror = _mirror(candidate_email="ada@example.com")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert len(zoho_client.sent) == 1
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_auto_reply_test_allowlist_falls_back_to_draft_for_other_addresses(session, monkeypatch):
    """A candidate email NOT on the allowlist falls back to draft_execute
    (or no-op, if that's also off) exactly as if auto-reply were off — the
    whole point of the allowlist is that unlisted candidates are unaffected."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS", "only-this-one@example.com")
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(candidate_email="a-real-candidate@gmail.com")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "draft_reply"
    assert zoho_client.sent == []
    assert len(zoho_client.drafts_created) == 1

    get_settings.cache_clear()


def test_auto_reply_never_passes_a_subject_kwarg_to_zoho(session, monkeypatch):
    """Regression guard: a real send discovered 2026-09-17 that Zoho's API
    rejects a `subject` field outright on both sendReply and draftReply
    ("An extra parameter 'subject' is found", HTTP 422) — a first attempt
    at tagging the outgoing subject for thread continuity passed one
    anyway and broke every live send until reverted. process_ticket must
    never pass `subject` to send_reply/create_draft_reply again until a
    real, Zoho-accepted mechanism is found."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(ticket_number="102802", subject="Password reset")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    process_ticket(session, zoho_client, mirror)

    assert "subject" not in zoho_client.sent[0]

    get_settings.cache_clear()


def test_auto_reply_empty_allowlist_means_no_restriction(session, monkeypatch):
    """Empty (default) allowlist — auto-send still applies to everyone,
    unchanged from before this setting existed."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(candidate_email="anyone@gmail.com")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    assert len(zoho_client.sent) == 1

    get_settings.cache_clear()


def test_auto_reply_never_sends_without_a_valid_candidate_email(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(candidate_email=None)
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "missing_candidate_email"
    assert zoho_client.sent == []
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_draft_execute_never_creates_a_draft_without_a_valid_candidate_email(
    session, monkeypatch
):
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(candidate_email="not-an-email")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert action.rule == "missing_candidate_email"
    assert zoho_client.sent == []
    assert zoho_client.drafts_created == []

    get_settings.cache_clear()


def test_ungrounded_question_still_escalates_not_sent(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror()
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=False)
    zoho_client = _FakeZohoClient()

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "route_to_human"
    assert zoho_client.sent == []

    get_settings.cache_clear()


def test_whatsapp_channel_is_unaffected_by_the_email_auto_reply_flag(session, monkeypatch):
    """Regression guard, mirrored from the reverse test in
    test_helpdesk_whatsapp.py: turning on email auto-send must not change
    WhatsApp's own, separately-gated behavior."""
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()

    mirror = _mirror(channel="WhatsApp")
    session.add(mirror)
    session.commit()

    _patch_common(monkeypatch, grounded=True)
    monkeypatch.setattr(
        helpdesk_ai_service, "generate_whatsapp_reply",
        lambda **k: "Try a different browser.",
    )
    zoho_client = _FakeZohoClient()
    zoho_client.send_whatsapp_reply = lambda ticket_id, content: None

    action = process_ticket(session, zoho_client, mirror)

    assert action.action_type == "auto_reply"
    # Sent via the WhatsApp path, not send_reply.
    assert zoho_client.sent == []

    get_settings.cache_clear()
