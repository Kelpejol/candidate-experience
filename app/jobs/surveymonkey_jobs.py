"""RQ job entry points for syncing SurveyMonkey survey responses into a campaign.

These functions are the callables enqueued onto the Redis-backed job queue
(app/core/queue.py); each opens its own DB session since RQ workers run the
job in a separate process/thread from whatever enqueued it.
"""

from sqlmodel import Session

from app.core.database import engine
from app.services.surveymonkey_sync_service import sync_campaign_survey_responses_for_campaign


def sync_campaign_survey_responses_job(campaign_id: str) -> dict:
    """Pull SurveyMonkey recipient/response data and update candidate survey status.

    Intended to be run as an RQ job. Opens a new DB session for the job's
    lifetime, delegates the SurveyMonkey API calls and candidate record
    updates to sync_campaign_survey_responses_for_campaign, and returns its
    summary dict (recipient/response counts, updated candidate count).
    """
    with Session(engine) as session:
        return sync_campaign_survey_responses_for_campaign(
            campaign_id=campaign_id,
            session=session,
        )