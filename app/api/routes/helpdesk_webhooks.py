import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import get_session
from app.core.queue import get_default_queue
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.jobs.helpdesk_ai_jobs import process_single_ticket_job
from app.services.helpdesk_ticket_mirror_service import upsert_ticket_mirror




router = APIRouter(prefix="/helpdesk/webhooks", tags=["helpdesk-webhooks"])


@router.post("/zoho/ticket")
async def zoho_ticket_event(request: Request, session: Session = Depends(get_session)):
    settings = get_settings()

    token = request.headers.get("X-Webhook-Token") or request.query_params.get("token")
    if not settings.zoho_webhook_token or not hmac.compare_digest(token or "", settings.zoho_webhook_token):
        raise HTTPException(status_code=401, detail="invalid webhook token")
    
    payload = await request.json()
    ticket_id = str(payload.get("ticketId") or payload.get("id") or "")
    if not ticket_id:
        raise HTTPException(status_code=422, detail="no ticket id in payload")
    

    client = build_zoho_desk_client(settings)
    ticket = client.get_ticket(ticket_id)

    mirror = upsert_ticket_mirror(session, ticket)
    session.commit()

    job = get_default_queue().enqueue(
        process_single_ticket_job,
        mirror.zoho_ticket_id,
        job_timeout=300,
        result_ttl=3600,
    )

    return {"status": "ok", "zoho_ticket_id": mirror.zoho_ticket_id, "ai_job_id": job.id}
