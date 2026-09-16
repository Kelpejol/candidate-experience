"""Tests for closing the outbound loop: webhook result → attempt + candidate,
and the stale-attempt sweep."""

from datetime import datetime, timedelta

from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.models.outbound_survey_answer import OutboundSurveyAnswer
from app.services.outbound_call_execution_service import (
    apply_outbound_call_webhook_result,
    sweep_stale_outbound_attempts,
)


def make_attempt(session, status="calling", started_at=None):
    campaign = Campaign(name="Dangote PRP", tool_name="FOT", status="outbound_calling")
    session.add(campaign)
    session.commit()
    candidate = CampaignCandidate(
        campaign_id=campaign.id,
        candidate_name="Aisha Bello",
        email="aisha@example.com",
        phone="+2348010000000",
        survey_status="non_responder",
        call_status="calling",
    )
    session.add(candidate)
    session.commit()
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id,
        candidate_id=candidate.id,
        candidate_name="Aisha Bello",
        phone="+2348010000000",
        attempt_number=1,
        status=status,
        started_at=started_at,
    )
    session.add(attempt)
    session.commit()
    return campaign, candidate, attempt


def outbound_payload(campaign_id, attempt_id, candidate_id, *, transfer=False, survey=False):
    return {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_result_1",
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "outbound_attempt_id": attempt_id,
                    "campaign_id": campaign_id,
                    "candidate_id": candidate_id,
                },
            },
            "metadata": {
                "features_usage": {"transfer_to_number": {"used": transfer}},
            },
            "analysis": {
                "transcript_summary": "Collected the survey by voice.",
                "data_collection_results": (
                    {"survey_completed": {"value": True}} if survey else {}
                ),
            },
            "transcript": [
                {"role": "agent", "message": "Hello, quick survey?"},
                {"role": "user", "message": "Sure."},
            ],
        },
    }


def test_webhook_result_updates_attempt_and_candidate(session):
    campaign, candidate, attempt = make_attempt(session)
    payload = outbound_payload(campaign.id, attempt.id, candidate.id, survey=True)

    updated = apply_outbound_call_webhook_result(session, payload)

    assert updated is not None
    assert updated.status == "responded_by_call"
    assert updated.transcript == "agent: Hello, quick survey?\nuser: Sure."
    assert updated.summary == "Collected the survey by voice."
    assert updated.elevenlabs_conversation_id == "conv_result_1"
    assert updated.ended_at is not None

    session.refresh(candidate)
    assert candidate.call_status == "responded_by_call"


def test_webhook_transfer_marks_handed_off(session):
    campaign, candidate, attempt = make_attempt(session)
    payload = outbound_payload(campaign.id, attempt.id, candidate.id, transfer=True)

    updated = apply_outbound_call_webhook_result(session, payload)

    assert updated.status == "handed_off_to_human"
    session.refresh(candidate)
    assert candidate.call_status == "handed_off_to_human"


def test_webhook_preserves_caller_opt_out(session):
    # Caller opted out mid-call (real-time tool); the later webhook must not
    # downgrade them to "answered".
    campaign, candidate, attempt = make_attempt(session, status="opted_out")
    candidate.opted_out_call = True
    candidate.call_status = "opted_out"
    attempt.status = "opted_out"
    session.add_all([candidate, attempt])
    session.commit()

    # A plain connected-call payload: no transfer, no survey-completion flag.
    payload = outbound_payload(campaign.id, attempt.id, candidate.id)
    updated = apply_outbound_call_webhook_result(session, payload)

    assert updated.status == "opted_out"
    assert updated.disposition == "caller_opted_out"
    session.refresh(candidate)
    assert candidate.call_status == "opted_out"
    assert candidate.opted_out_call is True


def test_webhook_completion_backed_by_captured_answers(session):
    # No survey-completion flag in the payload, but we captured answers via the
    # real-time tool -> still counts as responded_by_call.
    campaign, candidate, attempt = make_attempt(session)
    session.add(
        OutboundSurveyAnswer(
            outbound_attempt_id=attempt.id,
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            question="How satisfied?",
            answer="Very",
        )
    )
    session.commit()

    payload = outbound_payload(campaign.id, attempt.id, candidate.id, survey=False)
    updated = apply_outbound_call_webhook_result(session, payload)

    assert updated.status == "responded_by_call"
    session.refresh(candidate)
    assert candidate.call_status == "responded_by_call"


def test_inbound_webhook_is_a_noop(session):
    payload = {"type": "post_call_transcription", "data": {"conversation_id": "c"}}
    assert apply_outbound_call_webhook_result(session, payload) is None


def test_unknown_attempt_is_a_noop(session):
    payload = outbound_payload("no-campaign", "no-attempt", "no-candidate")
    assert apply_outbound_call_webhook_result(session, payload) is None


def test_sweep_marks_stale_calling_attempts_no_answer(session):
    campaign, candidate, attempt = make_attempt(
        session, status="calling", started_at=datetime.utcnow() - timedelta(minutes=30)
    )

    result = sweep_stale_outbound_attempts(session, older_than_minutes=15)

    assert result["swept"] == 1
    session.refresh(attempt)
    session.refresh(candidate)
    assert attempt.status == "no_answer"
    assert candidate.call_status == "no_answer"


def test_sweep_leaves_recent_calling_attempts(session):
    make_attempt(session, status="calling", started_at=datetime.utcnow())

    result = sweep_stale_outbound_attempts(session, older_than_minutes=15)

    assert result["swept"] == 0
