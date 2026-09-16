"""RQ job entry point for the campaign orchestration scheduler.

Advances every active campaign one step through its lifecycle
(survey_sent -> outbound_ready -> outbound_calling -> completed) using
campaign_orchestration_service. Enqueued on a cron by
scripts/run_campaign_scheduler.py; opens its own DB session since RQ workers
run the job in a separate process.

Nothing here places a call directly — advance_campaign delegates to the
existing outbound execution service, which stays gated on the (still-blocked)
telephony path. Until then this safely advances campaigns up to "queue built".
"""

import logging

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.models.campaign import Campaign
from app.services.campaign_orchestration_service import advance_campaign
from app.services.outbound_call_execution_service import (
    sweep_stale_outbound_attempts,
)

# Only these states have a next step for the orchestrator. draft/uploaded are
# still being set up; survey_sending/non_response_checking are transient
# operation-in-progress states; completed/failed are terminal.
ORCHESTRATED_STATUSES = (
    "survey_sent",
    "waiting_for_responses",
    "outbound_ready",
    "outbound_calling",
)


def run_campaign_orchestration_job() -> dict:
    """Advance every active campaign by one step; return an action tally."""
    settings = get_settings()
    concurrency = settings.campaign_outbound_concurrency
    max_attempts = settings.campaign_outbound_max_attempts

    tally: dict[str, int] = {}
    errors = 0
    with Session(engine) as session:
        # Recover calls that never connected BEFORE deciding what to do.
        # ElevenLabs' post-call webhook only fires for calls that were
        # answered, so a no-answer/busy/voicemail attempt would sit in
        # "calling" forever — the orchestrator would wait on it indefinitely
        # and auto-retry (whose whole purpose is the no-answer case) would
        # never fire. Sweeping first turns those into "no_answer" so this same
        # tick can act on them.
        try:
            swept = sweep_stale_outbound_attempts(session)
        except Exception:
            session.rollback()
            logging.exception("Stale outbound sweep failed")
            swept = {}

        campaigns = session.exec(
            select(Campaign).where(Campaign.status.in_(ORCHESTRATED_STATUSES))
        ).all()
        for campaign in campaigns:
            # Isolate each campaign: one campaign's failure (a flaky external
            # call, bad data) must not stop the others from advancing. Roll the
            # session back so a partial write doesn't leak into the next one.
            try:
                result = advance_campaign(
                    session,
                    campaign,
                    concurrency=concurrency,
                    max_attempts=max_attempts,
                )
                action = result.get("action", "none")
            except Exception:
                session.rollback()
                errors += 1
                logging.exception(
                    "Orchestration failed for campaign %s", campaign.id
                )
                action = "error"
            tally[action] = tally.get(action, 0) + 1

    return {
        "campaigns": len(campaigns),
        "actions": tally,
        "errors": errors,
        "swept": swept,
    }
