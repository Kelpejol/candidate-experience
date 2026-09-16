"""Voice-agent campaign resolution.

The agent asks the caller which campaign they're calling about, then calls this
to validate it: is it a known, currently-active campaign (returns the canonical
name to confirm), inactive, ambiguous (ask the caller to narrow), or not found?
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.routes.voice_kb import require_tool_token
from app.core.database import get_session
from app.schemas.voice_kb import CampaignResolveRequest, CampaignResolveResponse
from app.services.campaign_scope_service import resolve_campaign

router = APIRouter(prefix="/voice-agent/campaign", tags=["Voice Agent Campaign"])


@router.post(
    "/resolve",
    response_model=CampaignResolveResponse,
    dependencies=[Depends(require_tool_token)],
)
def resolve(payload: CampaignResolveRequest, session: Session = Depends(get_session)):
    """Validate the campaign name the caller gave against current campaigns."""
    return resolve_campaign(session, payload.name)
