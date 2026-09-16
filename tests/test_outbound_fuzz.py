"""Adversarial input tests for the outbound agent's live tools.

The /voice-agent/outbound/* endpoints are called by a third party (ElevenLabs)
with values derived from speech, so they receive whatever the model produces:
odd unicode, enormous strings, wrong types, missing fields, and — if anything
were ever misconfigured — ids belonging to a different campaign. None of that
may 500, corrupt data, or cross a campaign boundary.
"""

import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.models.outbound_survey_answer import OutboundSurveyAnswer
from app.services.outbound_survey_service import MAX_ANSWER_LENGTH


@pytest.fixture(autouse=True)
def _open_endpoints(monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _attempt(session, campaign_name="Fuzz"):
    campaign = Campaign(name=campaign_name, survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    candidate = CampaignCandidate(
        campaign_id=campaign.id, candidate_name="Ada", phone="+2348010000000",
        survey_status="non_responder", call_status="calling",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id=candidate.id,
        phone="+2348010000000", attempt_number=1, status="calling",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return campaign, candidate, attempt


# --- hostile strings -------------------------------------------------------

HOSTILE = [
    "'; DROP TABLE outboundcallattempt; --",   # sql-injection shaped
    "<script>alert(1)</script>",               # html/script
    "Ada\x00Bello",                          # embedded null byte
    "🎉🎉🎉 emoji answer 🎉",                    # astral-plane unicode
    "  \t\n  padded  \t\n  ",                  # whitespace to be trimmed
    "أنا متاح يوم الاثنين",                     # rtl script
    "a" * 50_000,                              # very large payload
    "line1\nline2\rline3",                     # embedded newlines
]


@pytest.mark.parametrize("value", HOSTILE)
def test_answer_accepts_hostile_text_without_error(client, session, value):
    campaign, _, attempt = _attempt(session)
    resp = client.post(
        "/voice-agent/outbound/answer",
        json={
            "outbound_attempt_id": attempt.id,
            "campaign_id": campaign.id,
            "question": "How are you?",
            "answer": value,
            "position": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    # Stored verbatim apart from the documented trim + length cap; nothing
    # executed, no crash.
    row = session.exec(select(OutboundSurveyAnswer)).first()
    assert row.answer == value.strip()[:MAX_ANSWER_LENGTH]
    assert len(row.answer) <= MAX_ANSWER_LENGTH

    # The table still exists and holds exactly one row (no injection took hold).
    assert len(session.exec(select(OutboundSurveyAnswer)).all()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},                                              # nothing at all
        {"outbound_attempt_id": "x"},                    # missing the rest
        {"outbound_attempt_id": "", "campaign_id": "",
         "question": "q", "answer": "a"},                # empty ids
        {"outbound_attempt_id": None, "campaign_id": None,
         "question": None, "answer": None},              # nulls
        {"outbound_attempt_id": 123, "campaign_id": [],
         "question": {"a": 1}, "answer": True},          # wrong types
        {"outbound_attempt_id": "x", "campaign_id": "y",
         "question": "q", "answer": "a", "position": "first"},   # bad position type
        {"outbound_attempt_id": "x", "campaign_id": "y",
         "question": "q", "answer": "a", "position": -2**63},    # extreme position
    ],
)
def test_answer_malformed_payloads_are_rejected_cleanly(client, payload):
    resp = client.post("/voice-agent/outbound/answer", json=payload)
    # Validation (422), a clean not-found (404), or a bad-request (400) — never a 500.
    assert resp.status_code in (400, 404, 422), resp.text


def test_answer_blank_is_rejected(client, session):
    campaign, _, attempt = _attempt(session)
    resp = client.post(
        "/voice-agent/outbound/answer",
        json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id,
              "question": "   ", "answer": "   "},
    )
    assert resp.status_code in (400, 422)


# --- campaign isolation (the security boundary) ----------------------------

def test_answer_cannot_cross_campaign_boundary(client, session):
    """An attempt id from campaign A must not be writable under campaign B."""
    campaign_a, _, attempt_a = _attempt(session, "Campaign A")
    campaign_b, _, _ = _attempt(session, "Campaign B")

    resp = client.post(
        "/voice-agent/outbound/answer",
        json={
            "outbound_attempt_id": attempt_a.id,   # A's attempt…
            "campaign_id": campaign_b.id,          # …claimed under B
            "question": "q",
            "answer": "a",
        },
    )
    assert resp.status_code == 404
    assert session.exec(select(OutboundSurveyAnswer)).all() == []


def test_opt_out_cannot_cross_campaign_boundary(client, session):
    campaign_a, candidate_a, attempt_a = _attempt(session, "Campaign A")
    campaign_b, _, _ = _attempt(session, "Campaign B")

    resp = client.post(
        "/voice-agent/outbound/opt-out",
        json={"outbound_attempt_id": attempt_a.id, "campaign_id": campaign_b.id},
    )
    assert resp.status_code == 404

    # A's candidate was NOT opted out by a call referencing B.
    session.refresh(candidate_a)
    assert candidate_a.opted_out_call is False


def test_answers_listing_only_returns_its_own_campaign(client, session):
    campaign_a, _, attempt_a = _attempt(session, "Campaign A")
    campaign_b, _, attempt_b = _attempt(session, "Campaign B")

    for campaign, attempt, answer in (
        (campaign_a, attempt_a, "answer-A"),
        (campaign_b, attempt_b, "answer-B"),
    ):
        client.post(
            "/voice-agent/outbound/answer",
            json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id,
                  "question": "q", "answer": answer, "position": 1},
        )

    rows = client.get(f"/campaigns/{campaign_a.id}/outbound/answers").json()
    assert [r["answer"] for r in rows] == ["answer-A"]


# --- repeated / duplicated tool calls --------------------------------------

def test_repeated_opt_out_is_idempotent(client, session):
    campaign, candidate, attempt = _attempt(session)
    body = {"outbound_attempt_id": attempt.id, "campaign_id": campaign.id}
    for _ in range(3):
        assert client.post("/voice-agent/outbound/opt-out", json=body).status_code == 200

    session.refresh(candidate)
    session.refresh(attempt)
    assert candidate.opted_out_call is True
    assert attempt.status == "opted_out"


def test_answer_correction_replaces_not_duplicates(client, session):
    """A candidate changing their mind mid-sentence must not create two rows."""
    campaign, _, attempt = _attempt(session)
    for answer in ("Yes", "Actually no", "Sorry — yes"):
        client.post(
            "/voice-agent/outbound/answer",
            json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id,
                  "question": "Will you attend?", "answer": answer, "position": 1},
        )

    rows = session.exec(select(OutboundSurveyAnswer)).all()
    assert len(rows) == 1
    assert rows[0].answer == "Sorry — yes"


def test_same_question_without_position_still_upserts(client, session):
    """When the agent omits position, the question text is the dedupe key."""
    campaign, _, attempt = _attempt(session)
    for answer in ("first", "second"):
        client.post(
            "/voice-agent/outbound/answer",
            json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id,
                  "question": "Any comments?", "answer": answer},
        )

    rows = session.exec(select(OutboundSurveyAnswer)).all()
    assert len(rows) == 1
    assert rows[0].answer == "second"


# --- auth ------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,body",
    [
        ("/voice-agent/outbound/questions", {"campaign_id": "x"}),
        ("/voice-agent/outbound/answer",
         {"outbound_attempt_id": "x", "campaign_id": "y", "question": "q", "answer": "a"}),
        ("/voice-agent/outbound/opt-out",
         {"outbound_attempt_id": "x", "campaign_id": "y"}),
    ],
)
def test_tools_reject_missing_token_when_configured(client, monkeypatch, path, body):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "s3cret")
    get_settings.cache_clear()
    assert client.post(path, json=body).status_code == 401
    get_settings.cache_clear()


@pytest.mark.parametrize("header", ["s3cret", "Bearer s3cret"])
def test_tools_accept_token_with_or_without_bearer(client, session, monkeypatch, header):
    campaign, _, attempt = _attempt(session)
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "s3cret")
    get_settings.cache_clear()
    resp = client.post(
        "/voice-agent/outbound/opt-out",
        json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id},
        headers={"Authorization": header},
    )
    assert resp.status_code == 200
    get_settings.cache_clear()
