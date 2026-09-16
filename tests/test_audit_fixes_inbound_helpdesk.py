"""Regressions for defects found auditing the inbound agent and the helpdesk.

Each pins a specific failure that was live in the code: the agent reading
off-topic content aloud, an escalation marker becoming a customer-facing reply,
a hallucinated category skipping the sensitive-ticket gate, complaint wording in
the body being ignored, and a webhook shape that lost the whole call record.
"""

from datetime import datetime, timedelta

import pytest

from app.core.helpdesk_taxonomy import SENSITIVE_CATEGORIES
from app.services import voice_kb_retrieval
from app.services.elevenlabs_webhook_mapper import (
    extract_candidate_phone,
    flatten_transcript,
    map_post_call_transcription_to_call_record,
    transfer_to_human_used,
)
from app.services.helpdesk_classifier import TicketClassification
from app.services.helpdesk_decision import decide_ticket_action
from app.services.helpdesk_ticket_mirror_service import upsert_ticket_mirror


# --- inbound: the grounding gate -------------------------------------------

def test_off_topic_hit_no_longer_answers_aloud(monkeypatch):
    """A 0.50 hit is out-of-scope per the calibration; it must escalate.

    The gate was an uncalibrated 0.55 — above the out-of-scope floor — so the
    agent would confidently read a near-miss chunk to a caller instead of
    handing off.
    """
    monkeypatch.setattr(voice_kb_retrieval, "embed_text", lambda q, timeout=None: [1.0])
    monkeypatch.setattr(
        voice_kb_retrieval, "query",
        lambda emb, k=3, campaign=None, extra_scopes=None: [
            {"distance": 0.50, "document": "off-topic text",
             "metadata": {"source": "misc.md"}},
        ],
    )
    result = voice_kb_retrieval.retrieve_for_answer("can I bring my phone?")
    assert result["answer_available"] is False


def test_genuine_hit_still_answers(monkeypatch):
    monkeypatch.setattr(voice_kb_retrieval, "embed_text", lambda q, timeout=None: [1.0])
    monkeypatch.setattr(
        voice_kb_retrieval, "query",
        lambda emb, k=3, campaign=None, extra_scopes=None: [
            {"distance": 0.28, "document": "how to reschedule",
             "metadata": {"source": "faq.md"}},
        ],
    )
    result = voice_kb_retrieval.retrieve_for_answer("how do I reschedule?")
    assert result["answer_available"] is True
    assert result["content"] == ["how to reschedule"]


# --- inbound: webhook shapes that used to 500 ------------------------------

def _payload(**data_overrides):
    data = {
        "conversation_id": "conv_1",
        "metadata": {"start_time_unix_secs": 1756000000, "call_duration_secs": 42},
        "analysis": {"transcript_summary": "ok"},
        "transcript": [{"role": "agent", "message": "hello"}],
    }
    data.update(data_overrides)
    return {"type": "post_call_transcription", "data": data}


@pytest.mark.parametrize(
    "overrides",
    [
        {"transcript": None},                       # call ended with no turns
        {"transcript": ["a string, not a dict"]},
        {"metadata": {"features_usage": None}},
        {"metadata": {"features_usage": {"transfer_to_number": None}}},
        {"metadata": {"start_time_unix_secs": "not-a-number"}},
        {"metadata": {"start_time_unix_secs": 99999999999999}},
        {"metadata": {"start_time_unix_secs": 1756000000, "call_duration_secs": "x"}},
    ],
)
def test_malformed_but_signed_payloads_still_map(overrides):
    """These shapes used to raise, 500 the webhook, and lose the call record —
    along with the outbound-attempt update that runs after it."""
    record = map_post_call_transcription_to_call_record(_payload(**overrides))
    assert record.external_call_id == "conv_1"


def test_null_transcript_flattens_to_none():
    assert flatten_transcript(_payload(transcript=None)) is None


def test_null_features_usage_is_not_a_transfer():
    assert transfer_to_human_used(_payload(metadata={"features_usage": None})) is False


def test_candidate_phone_is_extracted_for_csat():
    """Without this, every auto-created CSAT invitation stalls unresolved."""
    payload = _payload(
        metadata={"phone_call": {"external_number": "+2348010000001"}}
    )
    assert extract_candidate_phone(payload) == "+2348010000001"
    assert map_post_call_transcription_to_call_record(payload).candidate_phone == (
        "+2348010000001"
    )


# --- helpdesk: the sensitive-ticket gate -----------------------------------

def _classification(category, sensitive=False):
    return TicketClassification(
        issue_category=category, tool_name=None, campaign_name=None,
        sensitivity_detected=sensitive, confidence_label="high", reason="x",
    )


@pytest.mark.parametrize(
    "hallucinated",
    ["Complaint", "results_question", "result_dispute", "legal_request", "", "payment"],
)
def test_off_taxonomy_category_routes_to_a_human(hallucinated):
    """An unrecognised label must not fall through to the permissive default —
    routing rules match category names exactly, so a capitalisation slip used
    to turn the strictest rule into an auto-draft."""
    classification = _classification(hallucinated)
    assert classification.issue_category in SENSITIVE_CATEGORIES
    assert decide_ticket_action(classification, subject="hi").action == "route_to_human"


def test_known_category_is_unchanged_and_still_draftable():
    classification = _classification("technical_issue")
    assert classification.issue_category == "technical_issue"
    assert decide_ticket_action(classification, subject="hi").action == "draft_reply"


def test_complaint_wording_in_the_body_is_caught():
    """Candidates file a neutral subject and escalate in the message."""
    decision = decide_ticket_action(
        _classification("technical_issue"),
        subject="Test page not loading",
        body="This is unacceptable, I will sue you.",
    )
    assert decision.action == "route_to_human"
    assert decision.rule == "complaint_keyword_backstop"


# --- helpdesk: the mirror's change signal ----------------------------------

# --- inbound: resolving the campaign a caller says -------------------------

def _campaigns(session, *names, inactive=()):
    from app.models.campaign import Campaign
    for name in names:
        session.add(Campaign(name=name, inbound_active=name not in inactive))
    session.commit()


@pytest.mark.parametrize(
    "spoken",
    [
        "Dangote PRP Graduate Trainee Feedback",    # ASR: no em dash
        "dangote prp graduate trainee - feedback",  # hyphen, lowercase
        "Dangote",
    ],
)
def test_spoken_campaign_name_matches_a_stored_em_dash_name(session, spoken):
    """Stored names are typed with em dashes; speech recognition never produces
    one, so a caller saying the full name used to get 'not found'."""
    from app.services.campaign_scope_service import resolve_campaign
    _campaigns(session, "Dangote PRP Graduate Trainee — Feedback")

    result = resolve_campaign(session, spoken)
    assert result["status"] == "found"
    assert result["name"] == "Dangote PRP Graduate Trainee — Feedback"


def test_a_garbled_fragment_does_not_resolve_to_a_campaign(session):
    """A stray letter used to match a sponsor and hand over its knowledge base."""
    from app.services.campaign_scope_service import resolve_campaign
    _campaigns(session, "Chevron Operator Skills — CSAT")

    for fragment in ("v", "e", "on", ""):
        assert resolve_campaign(session, fragment)["status"] == "not_found"


def test_an_archived_campaign_does_not_make_the_live_one_ambiguous(session):
    """Last cycle's switched-off campaign must not block this cycle's caller."""
    from app.services.campaign_scope_service import resolve_campaign
    _campaigns(
        session,
        "Shell Graduate 2026",
        "Shell Graduate 2025 archived",
        inactive=("Shell Graduate 2025 archived",),
    )

    result = resolve_campaign(session, "Shell Graduate")
    assert result["status"] == "found"
    assert result["name"] == "Shell Graduate 2026"


def test_a_switched_off_campaign_reports_inactive_not_missing(session):
    from app.services.campaign_scope_service import resolve_campaign
    _campaigns(session, "Shell Graduate 2025", inactive=("Shell Graduate 2025",))

    result = resolve_campaign(session, "Shell Graduate")
    assert result["status"] == "inactive"
    assert result["name"] == "Shell Graduate 2025"


# --- helpdesk: the mirror's change signal ----------------------------------

def test_mirror_records_zoho_modified_time(session):
    mirror = upsert_ticket_mirror(session, {
        "id": "77", "channel": "Email", "status": "Open",
        "modifiedTime": "2026-08-20T10:00:00.000Z",
    })
    session.commit()
    assert mirror.zoho_modified_at == datetime(2026, 8, 20, 10, 0)


def test_mirror_survives_a_null_channel(session):
    """Zoho sends the key with a null value; channel is NOT NULL."""
    mirror = upsert_ticket_mirror(session, {"id": "78", "channel": None, "status": "Open"})
    session.commit()
    assert mirror.channel == "Email"


def test_sync_does_not_blank_the_ai_classification(session):
    upsert_ticket_mirror(session, {"id": "79", "channel": "Email", "status": "Open"})
    session.commit()
    mirror = upsert_ticket_mirror(session, {"id": "79", "channel": "Email", "status": "Open"})
    mirror.issue_category = "technical_issue"
    session.commit()

    # A later sync with no category from Zoho must keep what the AI worked out.
    again = upsert_ticket_mirror(session, {"id": "79", "channel": "Email", "status": "Open"})
    session.commit()
    assert again.issue_category == "technical_issue"
