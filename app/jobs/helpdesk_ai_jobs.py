"""RQ job entry points for the Helpdesk AI pipeline.

Each function opens its own DB session since RQ workers run the job in a
separate process/thread from whatever enqueued it.
"""

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import process_pending_tickets, process_ticket
from app.services.helpdesk_ticket_mirror_service import sync_ticket_mirror_from_zoho


def process_single_ticket_job(zoho_ticket_id: str) -> dict:
    """Classify/decide/draft one ticket. Enqueued by the webhook so the
    HTTP response to Zoho stays fast — the LLM/embedding calls happen here,
    off the request path, not inline in the webhook handler.
    """
    settings = get_settings()
    zoho_client = build_zoho_desk_client(settings)

    with Session(engine) as session:
        mirror = session.exec(
            select(HelpdeskTicketMirror).where(
                HelpdeskTicketMirror.zoho_ticket_id == zoho_ticket_id
            )
        ).first()
        if mirror is None:
            return {"status": "skipped", "reason": "ticket not in mirror yet"}

        action = process_ticket(session, zoho_client, mirror)
        session.commit()
        return {"status": "ok", "action_type": action.action_type, "rule": action.rule}


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
    zoho_client = build_zoho_desk_client(settings)

    with Session(engine) as session:
        sync_result = sync_ticket_mirror_from_zoho(
            session=session,
            client=zoho_client,
            department_id=settings.zoho_department_id,
        )
        action_tally = process_pending_tickets(session, zoho_client)

    return {"synced": sync_result["synced"], "actions": action_tally}
