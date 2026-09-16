"""API routes for post-call CSAT.

Manual invitation creation per call, plus enqueue endpoints for the send and
sync jobs (sending real emails stays behind the RQ worker, not inline).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.queue import get_default_queue
from app.jobs.csat_jobs import send_pending_csat_job, sync_csat_responses_job
from app.models.csat_invitation import CsatInvitation
from app.schemas.csat import CsatInvitationRead
from app.schemas.job import JobQueuedRead
from app.services.call_record_service import get_call_record_by_external_id
from app.services.csat_service import create_csat_invitation_for_call
from app.services.job_queue_service import get_job_status_value

router = APIRouter(prefix="/csat", tags=["CSAT"])


@router.post(
    "/calls/{external_call_id}",
    response_model=CsatInvitationRead,
    status_code=201,
)
def create_csat_for_call(
    external_call_id: str,
    session: Session = Depends(get_session),
):
    """Create (idempotently) a CSAT invitation for a call.

    Resolves the candidate email from the call's phone; status reflects
    eligibility (ready_to_send / pending_contact_lookup / skipped_opt_out).
    404 if the call record doesn't exist.
    """
    call_record = get_call_record_by_external_id(external_call_id, session)
    if not call_record:
        raise HTTPException(status_code=404, detail="Call record not found")

    return create_csat_invitation_for_call(session=session, call_record=call_record)


@router.get("/calls/{external_call_id}", response_model=CsatInvitationRead)
def get_csat_for_call(
    external_call_id: str,
    session: Session = Depends(get_session),
):
    """Fetch the CSAT invitation for a call, or 404 if none exists."""
    call_record = get_call_record_by_external_id(external_call_id, session)
    if not call_record:
        raise HTTPException(status_code=404, detail="Call record not found")

    invitation = session.exec(
        select(CsatInvitation).where(
            CsatInvitation.call_record_id == call_record.id
        )
    ).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="No CSAT invitation for this call")

    return invitation


@router.get("", response_model=list[CsatInvitationRead])
def list_csat_invitations(
    session: Session = Depends(get_session),
    status: str | None = None,
):
    """List CSAT invitations, optionally filtered by status."""
    statement = select(CsatInvitation).order_by(CsatInvitation.created_at.desc())
    if status:
        statement = statement.where(CsatInvitation.status == status)
    return session.exec(statement).all()


@router.post("/jobs/send-pending", response_model=JobQueuedRead, status_code=202)
def enqueue_send_pending_csat():
    """Enqueue the job that sends all ready_to_send CSAT invitations."""
    job = get_default_queue().enqueue(
        send_pending_csat_job, job_timeout=600, result_ttl=3600
    )
    return {"job_id": job.id, "status": get_job_status_value(job)}


@router.post("/jobs/sync", response_model=JobQueuedRead, status_code=202)
def enqueue_sync_csat_responses():
    """Enqueue the job that syncs CSAT responses."""
    job = get_default_queue().enqueue(
        sync_csat_responses_job, job_timeout=600, result_ttl=3600
    )
    return {"job_id": job.id, "status": get_job_status_value(job)}
