import pytest

from app.core.config import get_settings
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services import outbound_survey_service
from tests.test_outbound_survey_service import SURVEY_DETAILS, _FakeSurveyMonkeyClient


def _seed_attempt(session):
    campaign = Campaign(name="Feedback Run", survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    candidate = CampaignCandidate(
        campaign_id=campaign.id,
        candidate_name="Ada",
        phone="+2348000000000",
        survey_status="non_responder",
        call_status="calling",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id,
        candidate_id=candidate.id,
        candidate_name="Ada",
        phone="+2348000000000",
        attempt_number=1,
        status="calling",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return campaign, candidate, attempt


@pytest.fixture(autouse=True)
def _open_endpoint(monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    # Swap the real SurveyMonkey client for a fake so the full route -> service
    # path runs without a network call.
    monkeypatch.setattr(
        outbound_survey_service,
        "_client",
        lambda: _FakeSurveyMonkeyClient(SURVEY_DETAILS),
    )
    yield
    get_settings.cache_clear()


def test_questions_endpoint_returns_spoken_questions(client, session):
    campaign = Campaign(name="Feedback Run", survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    resp = client.post("/voice-agent/outbound/questions", json={"campaign_id": campaign.id})
    assert resp.status_code == 200

    body = resp.json()
    assert body["campaign_id"] == campaign.id
    texts = [q["text"] for q in body["questions"]]
    assert "Was your issue resolved?" in texts
    assert body["questions"][0]["options"] == ["Yes", "No"]


def test_questions_endpoint_unknown_campaign_404(client):
    resp = client.post("/voice-agent/outbound/questions", json={"campaign_id": "nope"})
    assert resp.status_code == 404


def test_questions_endpoint_no_survey_400(client, session):
    campaign = Campaign(name="No Survey", survey_id=None)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    resp = client.post("/voice-agent/outbound/questions", json={"campaign_id": campaign.id})
    assert resp.status_code == 400


def test_answer_endpoint_saves(client, session):
    campaign, candidate, attempt = _seed_attempt(session)
    resp = client.post(
        "/voice-agent/outbound/answer",
        json={
            "outbound_attempt_id": attempt.id,
            "campaign_id": campaign.id,
            "question": "How satisfied were you?",
            "answer": "Very",
            "position": 1,
            "type": "choice",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] is True


def test_answer_endpoint_unknown_attempt_404(client, session):
    campaign = Campaign(name="x", survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    resp = client.post(
        "/voice-agent/outbound/answer",
        json={
            "outbound_attempt_id": "nope",
            "campaign_id": campaign.id,
            "question": "Q",
            "answer": "A",
        },
    )
    assert resp.status_code == 404


def test_list_answers_endpoint(client, session):
    campaign, candidate, attempt = _seed_attempt(session)
    # Record two answers via the tool endpoint, then read them back.
    for pos, (q, a) in enumerate(
        [("How satisfied?", "Very"), ("Any comments?", "Great experience")], start=1
    ):
        client.post(
            "/voice-agent/outbound/answer",
            json={
                "outbound_attempt_id": attempt.id,
                "campaign_id": campaign.id,
                "question": q,
                "answer": a,
                "position": pos,
            },
        )

    resp = client.get(f"/campaigns/{campaign.id}/outbound/answers")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert {r["answer"] for r in rows} == {"Very", "Great experience"}
    # Enriched with the candidate's name for grouping in the UI.
    assert all(r["candidate_name"] == "Ada" for r in rows)


def test_list_answers_unknown_campaign_404(client):
    resp = client.get("/campaigns/nope/outbound/answers")
    assert resp.status_code == 404


def test_opt_out_endpoint(client, session):
    campaign, candidate, attempt = _seed_attempt(session)
    resp = client.post(
        "/voice-agent/outbound/opt-out",
        json={"outbound_attempt_id": attempt.id, "campaign_id": campaign.id},
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] is True

    session.refresh(candidate)
    assert candidate.opted_out_call is True
    assert candidate.call_status == "opted_out"
