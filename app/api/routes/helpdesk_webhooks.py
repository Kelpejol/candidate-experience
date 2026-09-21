import hmac

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.queue import get_default_queue
from app.core.redis import get_redis_connection
from app.jobs.helpdesk_ai_jobs import process_single_ticket_job
from app.services.job_queue_service import (
    enqueue_unique_active_job,
    get_job_status_value,
)




router = APIRouter(prefix="/helpdesk/webhooks", tags=["helpdesk-webhooks"])


@router.post("/zoho/ticket")
async def zoho_ticket_event(request: Request):
    settings = get_settings()

    token = request.headers.get("X-Webhook-Token") or request.query_params.get("token")
    if not settings.zoho_webhook_token or not hmac.compare_digest(token or "", settings.zoho_webhook_token):
        raise HTTPException(status_code=401, detail="invalid webhook token")
    
    payload = await request.json()
    ticket_id = str(payload.get("ticketId") or payload.get("id") or "")
    if not ticket_id:
        raise HTTPException(status_code=422, detail="no ticket id in payload")

    try:
        queue = get_default_queue()
        redis_connection = get_redis_connection()
        job = enqueue_unique_active_job(
            queue=queue,
            redis_connection=redis_connection,
            lock_key=f"helpdesk:ticket:{ticket_id}:ai-job",
            func=process_single_ticket_job,
            args=(ticket_id,),
            job_timeout=300,
            result_ttl=3600,
            lock_ttl=900,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="could not enqueue helpdesk ticket job",
        ) from exc

    return {
        "status": "ok",
        "zoho_ticket_id": ticket_id,
        "ai_job_id": job.id,
        "ai_job_status": get_job_status_value(job),
    }
