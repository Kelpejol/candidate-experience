"""RQ job entry points for placing outbound voice calls to candidates.

These functions are the callables enqueued onto the Redis-backed job queue
(app/core/queue.py); each opens its own DB session since RQ workers run the
job in a separate process/thread from whatever enqueued it.
"""

from sqlmodel import Session

from app.core.database import engine
from app.services.outbound_call_execution_service import execute_next_outbound_call_for_campaign


def execute_next_outbound_call_job(campaign_id: str) -> dict:
    """Claim and place the next queued outbound call attempt for a campaign.

    Intended to be run as an RQ job (signature must stay pickleable/simple
    since RQ serializes args). Opens a new DB session for the job's lifetime,
    delegates the actual claim + ElevenLabs call creation + DB writes to
    execute_next_outbound_call_for_campaign, and returns its result dict.
    """
    with Session(engine) as session:
        return execute_next_outbound_call_for_campaign(
            campaign_id=campaign_id,
            session=session,
        )
