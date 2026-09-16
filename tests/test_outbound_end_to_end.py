"""End-to-end walk of a real outbound outreach campaign.

Drives the whole path the way production does — campaign created with call
context, candidates uploaded, queue built, call placed (provider mocked at the
network boundary only), the agent's live tools called mid-call, and the
post-call webhook closing the loop — then asserts the state an officer would
actually see afterwards.

Covers the happy path plus the two branches that change the outcome: a caller
who opts out, and a call nobody answers.
"""

from datetime import datetime

import pytest

from app.integrations import elevenlabs_outbound_client
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.core.config import get_settings
from app.services.outbound_call_execution_service import (
    apply_outbound_call_webhook_result,
    execute_next_outbound_call_for_campaign,
)
from sqlmodel import select


@pytest.fixture(autouse=True)
def _open_tool_endpoints(monkeypatch):
    """The agent's tools are token-guarded in prod; open them for the test."""
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


CAMPAIGN_PAYLOAD = {
    "name": "September Graduate Assessment",
    "tool_name": "FOT",
    "call_reason": "reminder",
    "organization_name": "Dragnet Solutions",
    "assessment_at": "2026-09-02T10:00:00",
    "assessment_location": "Lagos test centre",
    "practice_test_url": "https://practice.example.com",
    "contact_info": "0800-000-0000",
}


def _placed_calls(monkeypatch):
    """Mock the ElevenLabs boundary, capturing what the agent would receive."""
    captured = []

    def fake_create(attempt, campaign=None):
        captured.append(
            elevenlabs_outbound_client.build_outbound_dynamic_variables(
                attempt, campaign
            )
        )
        return {
            "batch_call_id": f"batch_{len(captured)}",
            "status": "pending",
            "total_calls_scheduled": 1,
            "total_calls_dispatched": 0,
        }

    monkeypatch.setattr(
        "app.services.outbound_call_execution_service.create_outbound_call_for_attempt",
        fake_create,
    )
    return captured


def _webhook(campaign_id, attempt_id, candidate_id, *, conv_id, transfer=False):
    return {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": conv_id,
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "outbound_attempt_id": attempt_id,
                    "campaign_id": campaign_id,
                    "candidate_id": candidate_id,
                },
            },
            "metadata": {"features_usage": {"transfer_to_number": {"used": transfer}}},
            "analysis": {"transcript_summary": "Confirmed attendance."},
            "transcript": [
                {"role": "agent", "message": "Am I speaking with Ada Bello?"},
                {"role": "user", "message": "Yes."},
            ],
        },
    }


def _setup_campaign_with_non_responder(client, session, name="Ada Bello"):
    """Create a campaign + one candidate who didn't answer the survey."""
    campaign_id = client.post("/campaigns", json=CAMPAIGN_PAYLOAD).json()["id"]

    client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[{"candidate_name": name, "email": "ada@example.com",
               "phone": "+2348010000001", "tool_name": "FOT"}],
    )
    candidate = session.exec(
        select(CampaignCandidate).where(CampaignCandidate.campaign_id == campaign_id)
    ).first()

    # The survey went out and they never responded.
    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate.id}/survey-status",
        json={"survey_status": "non_responder"},
    )
    session.refresh(candidate)
    return campaign_id, candidate


def test_full_outreach_call_happy_path(client, session, monkeypatch):
    captured = _placed_calls(monkeypatch)
    campaign_id, candidate = _setup_campaign_with_non_responder(client, session)

    # --- queue the call -----------------------------------------------------
    queued = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    assert queued.status_code == 201
    assert len(queued.json()) == 1

    # --- place it -----------------------------------------------------------
    result = execute_next_outbound_call_for_campaign(campaign_id, session)
    assert result["status"] == "calling"
    attempt_id = result["attempt_id"]

    # The agent got everything the script needs, all as real strings.
    variables = captured[0]
    assert variables["candidate_name"] == "Ada Bello"
    assert variables["call_reason"] == "reminder"
    assert variables["organization_name"] == "Dragnet Solutions"
    assert variables["assessment_location"] == "Lagos test centre"
    assert "September" in variables["assessment_at"]
    assert all(isinstance(v, str) for v in variables.values())

    # --- mid-call: the agent records what the candidate said ----------------
    for position, (question, answer) in enumerate(
        [
            ("Have you received the email invite?", "Yes"),
            ("Will you be available on that date?", "Yes"),
            ("Have you done the practice test?", "Not yet"),
        ],
        start=1,
    ):
        resp = client.post(
            "/voice-agent/outbound/answer",
            json={
                "outbound_attempt_id": attempt_id,
                "campaign_id": campaign_id,
                "question": question,
                "answer": answer,
                "position": position,
            },
        )
        assert resp.status_code == 200

    # --- after the call: the webhook closes the loop ------------------------
    updated = apply_outbound_call_webhook_result(
        session,
        _webhook(campaign_id, attempt_id, candidate.id, conv_id="conv_e2e_1"),
    )
    # Completion is backed by the answers WE captured, not the agent's say-so.
    assert updated.status == "responded_by_call"
    assert updated.elevenlabs_conversation_id == "conv_e2e_1"

    session.refresh(candidate)
    assert candidate.call_status == "responded_by_call"

    # --- what the officer sees ---------------------------------------------
    answers = client.get(f"/campaigns/{campaign_id}/outbound/answers").json()
    assert len(answers) == 3
    assert [a["answer"] for a in sorted(answers, key=lambda a: a["position"])] == [
        "Yes", "Yes", "Not yet",
    ]
    assert all(a["candidate_name"] == "Ada Bello" for a in answers)


def test_opt_out_survives_the_post_call_webhook(client, session, monkeypatch):
    _placed_calls(monkeypatch)
    campaign_id, candidate = _setup_campaign_with_non_responder(client, session)
    client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    attempt_id = execute_next_outbound_call_for_campaign(campaign_id, session)["attempt_id"]

    # Mid-call: "take me off your list."
    resp = client.post(
        "/voice-agent/outbound/opt-out",
        json={"outbound_attempt_id": attempt_id, "campaign_id": campaign_id},
    )
    assert resp.status_code == 200

    # The webhook lands afterwards and must NOT downgrade the opt-out.
    updated = apply_outbound_call_webhook_result(
        session, _webhook(campaign_id, attempt_id, candidate.id, conv_id="conv_e2e_2")
    )
    assert updated.status == "opted_out"

    session.refresh(candidate)
    assert candidate.opted_out_call is True
    assert candidate.call_status == "opted_out"

    # And they are never queued again.
    requeued = client.post(f"/campaigns/{campaign_id}/outbound/build-queue").json()
    assert requeued == []
    retried = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue").json()
    assert retried == []


def test_no_answer_is_retried_then_gives_up(client, session, monkeypatch):
    _placed_calls(monkeypatch)
    campaign_id, candidate = _setup_campaign_with_non_responder(client, session)
    client.post(f"/campaigns/{campaign_id}/outbound/build-queue")

    # Nobody picks up, three times over — the retry cap must stop the calling.
    for expected_attempt_number in (1, 2, 3):
        result = execute_next_outbound_call_for_campaign(campaign_id, session)
        assert result["status"] == "calling"
        attempt = session.get(OutboundCallAttempt, result["attempt_id"])
        assert attempt.attempt_number == expected_attempt_number

        # The stale sweep / provider reports no answer.
        client.patch(
            f"/campaigns/{campaign_id}/outbound/attempts/{attempt.id}/status",
            json={"status": "no_answer"},
        )
        client.post(f"/campaigns/{campaign_id}/outbound/retry-queue?max_attempts=3")

    # Cap reached: no fourth attempt is ever created.
    attempts = session.exec(
        select(OutboundCallAttempt).where(
            OutboundCallAttempt.campaign_id == campaign_id
        )
    ).all()
    assert len(attempts) == 3
    assert execute_next_outbound_call_for_campaign(campaign_id, session)["status"] == (
        "no_queued_attempt"
    )


def test_nameless_candidate_never_sends_none_to_the_agent(client, session, monkeypatch):
    # Candidates uploaded from a spreadsheet often have only a phone number.
    captured = _placed_calls(monkeypatch)
    campaign_id = client.post("/campaigns", json={"name": "Bare Campaign"}).json()["id"]
    client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[{"phone": "+2348010000009"}],
    )
    candidate = session.exec(
        select(CampaignCandidate).where(CampaignCandidate.campaign_id == campaign_id)
    ).first()
    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate.id}/survey-status",
        json={"survey_status": "non_responder"},
    )
    client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    execute_next_outbound_call_for_campaign(campaign_id, session)

    variables = captured[0]
    assert None not in variables.values()
    assert variables["candidate_name"] == ""      # prompt handles the empty case
    assert variables["organization_name"] == "Bare Campaign"  # falls back to name
