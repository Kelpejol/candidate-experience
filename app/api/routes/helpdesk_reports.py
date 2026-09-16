"""API route for Helpdesk AI reporting (automation rate, KB gaps, etc.)."""

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.helpdesk import HelpdeskAIActionRead
from app.services.helpdesk_reporting_service import (
    get_helpdesk_summary,
    list_recent_ai_actions,
)

router = APIRouter(prefix="/helpdesk/reports", tags=["helpdesk-reports"])


@router.get("/summary")
def helpdesk_summary(session: Session = Depends(get_session)):
    """Return the current helpdesk automation summary (see plan step 9)."""
    return get_helpdesk_summary(session)


@router.get("/actions", response_model=list[HelpdeskAIActionRead])
def helpdesk_ai_actions(
    session: Session = Depends(get_session),
    action_type: str | None = None,
    only_drafts: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Recent per-ticket AI decisions (newest first) for the review UI.

    Optional filters: `action_type` (draft_reply / route_to_human / tag_only)
    and `only_drafts=true` to show just the rows that produced a draft.
    """
    return list_recent_ai_actions(
        session, action_type=action_type, only_drafts=only_drafts, limit=limit
    )
