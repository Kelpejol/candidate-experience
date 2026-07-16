"""API route for Helpdesk AI reporting (automation rate, KB gaps, etc.)."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.services.helpdesk_reporting_service import get_helpdesk_summary

router = APIRouter(prefix="/helpdesk/reports", tags=["helpdesk-reports"])


@router.get("/summary")
def helpdesk_summary(session: Session = Depends(get_session)):
    """Return the current helpdesk automation summary (see plan step 9)."""
    return get_helpdesk_summary(session)
