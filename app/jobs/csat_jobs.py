"""RQ job entry points for CSAT: sending pending invitations and syncing
responses. Each opens its own DB session (RQ runs jobs in a separate process).
"""

from sqlmodel import Session

from app.core.database import engine
from app.services.csat_service import send_pending_csat, sync_csat_responses


def send_pending_csat_job() -> dict:
    """Send all ready_to_send CSAT invitations as one SurveyMonkey batch."""
    with Session(engine) as session:
        return send_pending_csat(session=session)


def sync_csat_responses_job() -> dict:
    """Pull CSAT responses and mark matching invitations responded."""
    with Session(engine) as session:
        return sync_csat_responses(session=session)
