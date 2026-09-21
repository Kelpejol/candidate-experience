"""RQ job entry points for the Helpdesk AI pipeline.

Each function opens its own DB session since RQ workers run the job in a
separate process/thread from whatever enqueued it.
"""

from uuid import uuid4

from redis import Redis
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.core.redis import get_redis_connection
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import (
    _latest_action_time,
    process_pending_tickets,
    process_ticket,
)
from app.services.helpdesk_ticket_mirror_service import (
    sync_ticket_mirror_from_zoho,
    upsert_ticket_mirror,
)

HELPDESK_PIPELINE_RUNNING_LOCK = "helpdesk:pipeline:running"
HELPDESK_PIPELINE_LOCK_TTL_SECONDS = 900
HELPDESK_TICKET_LOCK_TTL_SECONDS = 900


def _ticket_processing_lock_key(zoho_ticket_id: str) -> str:
    return f"helpdesk:ticket:{zoho_ticket_id}:processing"


def _acquire_lock(
    redis_connection: Redis,
    key: str,
    *,
    ttl_seconds: int,
) -> str | None:
    """Acquire a Redis lock and return its token, or None if already held."""
    token = str(uuid4())
    acquired = redis_connection.set(key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None


def _release_lock(redis_connection: Redis, key: str, token: str) -> None:
    """Release only the lock instance we acquired, leaving newer holders alone."""
    current = redis_connection.get(key)
    if isinstance(current, bytes):
        current = current.decode("utf-8")
    if current == token:
        redis_connection.delete(key)


class RedisTicketProcessingLock:
    """Best-effort per-ticket lock shared by webhook and polling workers."""

    def __init__(
        self,
        redis_connection: Redis,
        ttl_seconds: int = HELPDESK_TICKET_LOCK_TTL_SECONDS,
    ):
        self.redis_connection = redis_connection
        self.ttl_seconds = ttl_seconds

    def acquire(self, zoho_ticket_id: str) -> str | None:
        return _acquire_lock(
            self.redis_connection,
            _ticket_processing_lock_key(zoho_ticket_id),
            ttl_seconds=self.ttl_seconds,
        )

    def release(self, zoho_ticket_id: str, token: str) -> None:
        _release_lock(
            self.redis_connection,
            _ticket_processing_lock_key(zoho_ticket_id),
            token,
        )


def process_single_ticket_job(zoho_ticket_id: str) -> dict:
    """Classify/decide/draft one ticket. Enqueued by the webhook so the
    HTTP response to Zoho stays fast — the LLM/embedding calls happen here,
    off the request path, not inline in the webhook handler.
    """
    settings = get_settings()
    zoho_client = build_zoho_desk_client(settings)
    redis_connection = get_redis_connection()
    ticket_lock = RedisTicketProcessingLock(redis_connection)
    lock_token = ticket_lock.acquire(zoho_ticket_id)
    if lock_token is None:
        return {"status": "skipped", "reason": "ticket is already being processed"}

    try:
        with Session(engine) as session:
            # Fetch the current Zoho state in the worker, not in the webhook
            # request. This keeps webhook acknowledgement fast/durable, and an
            # out-of-order event still processes the latest ticket version.
            ticket = zoho_client.get_ticket(zoho_ticket_id)
            mirror = upsert_ticket_mirror(session, ticket)
            session.commit()

            # Idempotency: Zoho retries webhooks, so the same event can arrive
            # more than once. Compare against Zoho's OWN modification time, not
            # our last_synced_at — a sync clock changes on every fetch and would
            # make duplicate delivery look like a real candidate update.
            last_action_at = _latest_action_time(session, zoho_ticket_id)
            changed_at = mirror.zoho_modified_at
            if not settings.helpdesk_conversation_enabled and last_action_at is not None and (
                changed_at is None or last_action_at >= changed_at
            ):
                return {"status": "skipped", "reason": "already processed this version"}

            action = process_ticket(session, zoho_client, mirror)
            session.commit()
            return {"status": "ok", "action_type": action.action_type, "rule": action.rule}
    finally:
        ticket_lock.release(zoho_ticket_id, lock_token)


def run_helpdesk_pipeline_job() -> dict:
    """Sync the ticket mirror from Zoho, then classify/decide/draft every
    ticket that's new or has a new candidate message since we last looked.

    This is the automation trigger: enqueue this on a schedule (or from the
    webhook once it's unblocked) and the helpdesk requires no manual script
    runs — new tickets get a decision on their own. Nothing is sent to
    candidates; draft placement on Zoho is still gated by
    settings.helpdesk_draft_execute.
    """
    settings = get_settings()
    redis_connection = get_redis_connection()
    pipeline_token = _acquire_lock(
        redis_connection,
        HELPDESK_PIPELINE_RUNNING_LOCK,
        ttl_seconds=HELPDESK_PIPELINE_LOCK_TTL_SECONDS,
    )
    if pipeline_token is None:
        return {"status": "skipped", "reason": "helpdesk pipeline already running"}

    try:
        zoho_client = build_zoho_desk_client(settings)
        with Session(engine) as session:
            sync_result = sync_ticket_mirror_from_zoho(
                session=session,
                client=zoho_client,
                department_id=settings.zoho_department_id,
            )
            action_tally = process_pending_tickets(
                session,
                zoho_client,
                ticket_lock=RedisTicketProcessingLock(redis_connection),
            )

        return {"status": "ok", "synced": sync_result["synced"], "actions": action_tally}
    finally:
        _release_lock(
            redis_connection,
            HELPDESK_PIPELINE_RUNNING_LOCK,
            pipeline_token,
        )
