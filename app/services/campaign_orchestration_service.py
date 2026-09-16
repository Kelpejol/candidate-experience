"""Scheduled orchestration for the outbound campaign lifecycle.

Today a campaign is walked through its lifecycle by hand — someone calls the
sync endpoint after the wait window, then build-queue, then drains the calls.
This module is the "brain" that decides the next step on its own, so a
scheduled job (Part 2) can drive every active campaign without anyone poking
endpoints.

Lifecycle (existing statuses):

    draft -> uploaded -> survey_sent -> outbound_ready -> outbound_calling -> completed

Part 1 (this commit) is ONLY the pure decision function: given a campaign's
state, the current time, and how many outbound attempts are queued / in
flight, it returns the single next action to take. It performs no I/O, so the
timing + state rules are unit-testable without SurveyMonkey, telephony, or a
DB. Part 2 will execute these actions via the existing services and register
the scheduled job.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, func, select

from app.models.campaign import Campaign
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services.campaign_service import (
    build_outbound_call_queue,
    build_outbound_retry_queue,
)
from app.services.outbound_call_execution_service import (
    execute_next_outbound_call_for_campaign,
)
from app.services.surveymonkey_sync_service import (
    sync_campaign_survey_responses_for_campaign,
)

# Terminal status set by the orchestrator when a campaign has nothing left to
# do (all responded, no non-responders to call, or the call queue is drained).
STATUS_COMPLETED = "completed"

# The action decide_campaign_next_step returns. Kept as plain strings so the
# executor (Part 2) and tests can switch on them without importing an enum.
ACTION_WAIT = "wait"                 # not time yet / calls still in flight
ACTION_SYNC_RESPONSES = "sync_responses"   # wait window elapsed -> pull responses
ACTION_BUILD_QUEUE = "build_queue"   # non-responders known -> queue the calls
ACTION_DRAIN_CALLS = "drain_calls"   # queued attempts exist -> place next call(s)
ACTION_RETRY_QUEUE = "retry_queue"   # queue drained -> requeue retryable no-answers
ACTION_COMPLETE = "complete"         # nothing left to do -> mark completed
ACTION_NONE = "none"                 # campaign is not in an orchestrated state


def decide_campaign_next_step(
    campaign: Campaign,
    now: datetime,
    *,
    queued_count: int = 0,
    in_flight_count: int = 0,
) -> str:
    """Decide the single next orchestration action for a campaign. Pure.

    Args:
        campaign: the campaign row (uses status, survey_sent_at,
            response_wait_hours).
        now: current time (passed in, not read, so tests are deterministic).
        queued_count: OutboundCallAttempts still "queued" for this campaign.
        in_flight_count: attempts currently "calling" (awaiting a result).

    Returns one of the ACTION_* constants.
    """
    status = campaign.status

    # "survey_sent" and "waiting_for_responses" are the same orchestration
    # situation — the survey is out and we're waiting for the response window
    # before checking non-responders. (survey_sending / non_response_checking
    # are transient "operation in progress" states we deliberately leave alone.)
    if status in ("survey_sent", "waiting_for_responses"):
        # Can't detect non-responders until the response window has elapsed.
        if campaign.survey_sent_at is None:
            return ACTION_WAIT
        wait_until = campaign.survey_sent_at + timedelta(
            hours=campaign.response_wait_hours
        )
        return ACTION_SYNC_RESPONSES if now >= wait_until else ACTION_WAIT

    if status == "outbound_ready":
        # Non-responders have been identified; turn them into a call queue.
        return ACTION_BUILD_QUEUE

    if status == "outbound_calling":
        if queued_count > 0:
            return ACTION_DRAIN_CALLS
        if in_flight_count > 0:
            # Calls are out; wait for their webhooks / the stale sweep before
            # deciding the campaign is finished.
            return ACTION_WAIT
        return ACTION_COMPLETE

    # draft / uploaded / completed / anything else: not the scheduler's job.
    return ACTION_NONE


# --- Part 2: execute the decided action via the existing services ----------

def _count_attempts_by_status(session: Session, campaign_id: str, status: str) -> int:
    """Count this campaign's outbound attempts currently in `status`."""
    return session.exec(
        select(func.count())
        .select_from(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.status == status)
    ).one()


def _mark_completed(session: Session, campaign: Campaign) -> None:
    """Move a campaign to the terminal completed status."""
    campaign.status = STATUS_COMPLETED
    session.add(campaign)
    session.commit()
    session.refresh(campaign)


def advance_campaign(
    session: Session,
    campaign: Campaign,
    *,
    now: datetime | None = None,
    concurrency: int = 1,
    max_attempts: int = 3,
) -> dict:
    """Advance a single campaign by one orchestration step.

    Reads the campaign's queued/in-flight attempt counts, asks
    decide_campaign_next_step what to do, and performs that one step through
    the existing services. Handles the two "nothing left" cases the pure
    function can't see — a sync that finds everyone responded, and a queue
    build that finds no one to call — by completing the campaign instead of
    looping. Returns a small summary dict for the job tally/logs.
    """
    now = now or datetime.utcnow()
    queued = _count_attempts_by_status(session, campaign.id, "queued")
    in_flight = _count_attempts_by_status(session, campaign.id, "calling")

    action = decide_campaign_next_step(
        campaign, now, queued_count=queued, in_flight_count=in_flight
    )

    if action == ACTION_SYNC_RESPONSES:
        sync_campaign_survey_responses_for_campaign(campaign.id, session)
        session.refresh(campaign)
        # Sync advances to outbound_ready only when non-responders exist; if
        # it's still in a waiting state, everyone responded — nobody to call.
        if campaign.status in ("survey_sent", "waiting_for_responses"):
            _mark_completed(session, campaign)
        return {"action": action, "status": campaign.status}

    if action == ACTION_BUILD_QUEUE:
        attempts = build_outbound_call_queue(campaign.id, session)
        session.refresh(campaign)
        if not attempts:
            _mark_completed(session, campaign)
        return {"action": action, "queued": len(attempts), "status": campaign.status}

    if action == ACTION_DRAIN_CALLS:
        # Respect the concurrency cap: only fill the free call slots this tick.
        slots = max(0, concurrency - in_flight)
        placed = 0
        for _ in range(slots):
            result = execute_next_outbound_call_for_campaign(campaign.id, session)
            if result.get("status") == "no_queued_attempt":
                break
            placed += 1
        return {"action": action, "placed": placed}

    if action == ACTION_COMPLETE:
        # Before finishing, give retryable outcomes (no_answer/busy/voicemail/
        # failed) another attempt, up to the per-candidate cap. If any get
        # requeued, stay in outbound_calling and let the next tick drain them;
        # only complete once nothing retryable remains. build_outbound_retry_queue
        # increments attempt_number and skips candidates at/above the cap, so
        # this always terminates. max_attempts <= 1 disables auto-retry.
        if max_attempts > 1:
            retried = build_outbound_retry_queue(
                campaign.id, session, max_attempts=max_attempts
            )
            if retried:
                session.refresh(campaign)
                return {
                    "action": ACTION_RETRY_QUEUE,
                    "queued": len(retried),
                    "status": campaign.status,
                }

        _mark_completed(session, campaign)
        return {"action": action, "status": campaign.status}

    # ACTION_WAIT / ACTION_NONE: nothing to do this tick.
    return {"action": action}
