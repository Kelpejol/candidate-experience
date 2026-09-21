"""Database setup for the application.

Creates the SQLModel/SQLite engine from configured settings, and provides
helpers to create tables and to obtain per-request DB sessions.
"""

from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session

from app.core.config import get_settings
from app.models.call_record import CallRecord
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_conversation_turn import HelpdeskConversationTurn
from app.models.csat_invitation import CsatInvitation
from app.models.collected_datum import CollectedDatum
from app.models.outbound_survey_answer import OutboundSurveyAnswer

settings = get_settings()


# `check_same_thread=False` is required for SQLite because FastAPI/RQ may
# access the connection from a different thread than the one that created it.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


# SQLite defaults serialize writers and fail fast on contention, so concurrent
# RQ workers + webhook writers hit "database is locked". WAL lets readers run
# alongside a writer, and a busy_timeout makes a writer wait for the lock
# instead of erroring immediately. No-op for non-SQLite backends (e.g. Postgres
# in production), so it's safe to leave on.
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # wait up to 5s for a lock
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


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
