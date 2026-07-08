"""Database setup for the application.

Creates the SQLModel/SQLite engine from configured settings, and provides
helpers to create tables and to obtain per-request DB sessions.
"""

from sqlmodel import SQLModel, create_engine, Session

from app.core.config import get_settings
from app.models.call_record import CallRecord
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt

settings = get_settings()


# `check_same_thread=False` is required for SQLite because FastAPI/RQ may
# access the connection from a different thread than the one that created it.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables():
    """Create all tables registered on `SQLModel.metadata` if they don't exist yet.

    Side effect: issues DDL against the configured database via `engine`.
    Relies on the model imports above to register tables with SQLModel.
    """
    SQLModel.metadata.create_all(engine)


def get_session():
    """Yield a `Session` bound to the shared engine for use as a FastAPI dependency.

    Side effect: opens a DB connection/session for the duration of the
    request and closes it afterwards via the `with` block.
    """
    with Session(engine) as session:
        yield session