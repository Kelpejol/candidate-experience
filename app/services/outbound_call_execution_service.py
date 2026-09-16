"""Executes queued outbound candidate calls.

Claims the next queued OutboundCallAttempt for a campaign, submits it to the
ElevenLabs conversational voice agent, and records the outcome (provider
batch call id on success, failure reason/status on error) back onto the
attempt and the associated candidate.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.integrations.elevenlabs_outbound_client import create_outbound_call_for_attempt
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.schemas.campaign import OutboundCallAttemptStatusUpdate
from app.services.campaign_service import (
    claim_next_queued_outbound_attempt,
    get_campaign_by_id,
    get_campaign_candidate_by_id,
    get_outbound_call_attempt_by_id,
    update_outbound_call_attempt_status,
)
from app.services.elevenlabs_webhook_mapper import (
    extract_recording_url,
    flatten_transcript,
    get_outbound_context,
    survey_completed_on_call,
    transfer_to_human_used,
)
from app.services.outbound_survey_service import attempt_has_survey_answers


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

    campaign = get_campaign_by_id(attempt.campaign_id, session)

    try:
        provider_result = create_outbound_call_for_attempt(attempt, campaign)
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


def _sync_candidate_call_status(
    session: Session, attempt: OutboundCallAttempt, status: str
) -> None:
    """Mirror an attempt's terminal status onto its candidate's call_status."""
    candidate = get_campaign_candidate_by_id(
        campaign_id=attempt.campaign_id,
        candidate_id=attempt.candidate_id,
        session=session,
    )
    if candidate:
        # Opt-out is terminal: once a candidate has opted out of calls, a
        # later webhook must not flip them back to answered/responded.
        candidate.call_status = "opted_out" if candidate.opted_out_call else status
        session.add(candidate)
        session.commit()


def apply_outbound_call_webhook_result(
    session: Session, payload: dict
) -> OutboundCallAttempt | None:
    """Close the outbound loop: apply an ElevenLabs post-call webhook to the
    OutboundCallAttempt it came from, and mirror the outcome onto the candidate.

    Returns the updated attempt, or None when the payload isn't an outbound
    call, lacks a campaign id, or references an attempt we can't find (a stale
    or unknown attempt — a safe no-op).
    """
    context = get_outbound_context(payload)
    if not context or not context.get("campaign_id"):
        return None

    attempt = get_outbound_call_attempt_by_id(
        campaign_id=context["campaign_id"],
        attempt_id=context["outbound_attempt_id"],
        session=session,
    )
    if not attempt:
        return None

    data = payload.get("data") or {}
    analysis = data.get("analysis") or {}

    # Reconcile the final status with what the real-time tools recorded during
    # the call, so the (later-arriving) webhook can't undo them:
    #  - a caller opt-out is terminal — never downgrade it to "answered".
    #  - completion is backed by our OWN captured answers, not only the agent's
    #    self-reported data-collection field (which depends on agent config).
    if attempt.status == "opted_out":
        status = "opted_out"
    elif transfer_to_human_used(payload):
        status = "handed_off_to_human"
    elif survey_completed_on_call(payload) or attempt_has_survey_answers(
        session, attempt.id
    ):
        status = "responded_by_call"
    else:
        status = "answered"

    disposition = "caller_opted_out" if status == "opted_out" else status

    updated = update_outbound_call_attempt_status(
        attempt=attempt,
        status_update=OutboundCallAttemptStatusUpdate(
            status=status,
            disposition=disposition,
            elevenlabs_conversation_id=data.get("conversation_id"),
            transcript=flatten_transcript(payload),
            summary=analysis.get("transcript_summary"),
            recording_url=extract_recording_url(payload),
        ),
        session=session,
    )
    _sync_candidate_call_status(session, updated, status)
    return updated


def sweep_stale_outbound_attempts(
    session: Session, older_than_minutes: int = 15
) -> dict:
    """Fallback for outbound calls that never connected.

    ElevenLabs' post_call_transcription webhook only fires for *connected*
    calls, so an attempt that gets no answer / busy / voicemail would sit in
    "calling" forever. This marks any attempt still "calling" past the window
    as no_answer, which feeds the existing retry policy. (A richer per-recipient
    reconciliation via the ElevenLabs batch-status API can replace this later.)
    """
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    stale = session.exec(
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.status == "calling")
        .where(OutboundCallAttempt.started_at.is_not(None))
        .where(OutboundCallAttempt.started_at < cutoff)
    ).all()

    for attempt in stale:
        update_outbound_call_attempt_status(
            attempt=attempt,
            status_update=OutboundCallAttemptStatusUpdate(
                status="no_answer",
                disposition="no_answer_timeout",
                elevenlabs_conversation_id=attempt.elevenlabs_conversation_id,
                transcript=attempt.transcript,
                summary=attempt.summary,
                recording_url=attempt.recording_url,
            ),
            session=session,
        )
        _sync_candidate_call_status(session, attempt, "no_answer")

    return {"swept": len(stale)}
