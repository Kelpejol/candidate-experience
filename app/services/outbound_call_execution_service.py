"""Executes queued outbound candidate calls.

Claims the next queued OutboundCallAttempt for a campaign, submits it to the
ElevenLabs conversational voice agent, and records the outcome (provider
batch call id on success, failure reason/status on error) back onto the
attempt and the associated candidate.
"""

from datetime import datetime

from sqlmodel import Session

from app.integrations.elevenlabs_outbound_client import create_outbound_call_for_attempt
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services.campaign_service import (
    claim_next_queued_outbound_attempt,
    get_campaign_candidate_by_id,
)


def mark_outbound_attempt_failed(
    *,
    attempt: OutboundCallAttempt,
    session: Session,
    reason: str,
) -> OutboundCallAttempt:
    """Mark an outbound call attempt as failed and propagate that to its candidate.

    Sets attempt.status to "failed", stores a truncated failure reason as the
    disposition, and stamps ended_at. If the related candidate can be found,
    its call_status is also set to "failed". Commits the session and returns
    the refreshed attempt.
    """
    attempt.status = "failed"
    attempt.disposition = reason[:500]  # truncate arbitrary provider/exception text so it fits the disposition column
    attempt.ended_at = datetime.utcnow()
    session.add(attempt)

    candidate = get_campaign_candidate_by_id(
        campaign_id=attempt.campaign_id,
        candidate_id=attempt.candidate_id,
        session=session,
    )

    if candidate:
        candidate.call_status = "failed"
        session.add(candidate)

    session.commit()
    session.refresh(attempt)

    return attempt


def execute_next_outbound_call_for_campaign(
    campaign_id: str,
    session: Session,
) -> dict:
    """Claim and place the next queued outbound call for a campaign.

    Atomically claims the oldest "queued" OutboundCallAttempt (transitioning
    it to "calling"), then submits it to the ElevenLabs batch-calling API. On
    provider failure the attempt is marked "failed" via
    mark_outbound_attempt_failed; on success the attempt is updated with the
    ElevenLabs batch_call_id. Side effects: places a real outbound call via
    ElevenLabs and commits attempt/candidate changes to the database.

    Returns a result dict describing the outcome; provider errors are caught
    and reported in the dict rather than raised, so callers (e.g. background
    jobs) can process the next attempt regardless.
    """
    attempt = claim_next_queued_outbound_attempt(
        campaign_id=campaign_id,
        session=session,
    )

    if not attempt:
        return {
            "campaign_id": campaign_id,
            "attempt_id": None,
            "status": "no_queued_attempt",
        }

    try:
        provider_result = create_outbound_call_for_attempt(attempt)
    except Exception as exc:
        # Any failure to reach/validate against ElevenLabs (network, auth,
        # missing config, bad phone number, etc.) fails just this attempt
        # instead of raising, so the caller can keep draining the queue.
        failed_attempt = mark_outbound_attempt_failed(
            attempt=attempt,
            session=session,
            reason=str(exc),
        )

        return {
            "campaign_id": campaign_id,
            "attempt_id": failed_attempt.id,
            "status": failed_attempt.status,
            "error": str(exc),
        }

    attempt.disposition = "elevenlabs_batch_call_created"
    attempt.summary = f"ElevenLabs batch_call_id={provider_result['batch_call_id']}"
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    return {
        "campaign_id": campaign_id,
        "attempt_id": attempt.id,
        "status": attempt.status,
        "provider": "elevenlabs",
        "provider_result": provider_result,
    }
