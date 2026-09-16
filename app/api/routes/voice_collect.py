"""Voice-agent data collection.

The agent asks the caller for a value (e.g. their email), reads it back to
confirm it captured correctly, then calls this to SAVE it. Only ever the
caller's own provided value — never anything looked up from our records.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.routes.voice_kb import require_tool_token
from app.core.database import get_session
from app.models.collected_datum import CollectedDatum
from app.schemas.voice_kb import CollectRequest, CollectResponse

router = APIRouter(prefix="/voice-agent", tags=["Voice Agent Collect"])


@router.post(
    "/collect",
    response_model=CollectResponse,
    dependencies=[Depends(require_tool_token)],
)
def collect(payload: CollectRequest, session: Session = Depends(get_session)):
    """Save one confirmed, caller-provided value against the call/campaign."""
    field = payload.field.strip()
    value = payload.value.strip()
    if not field or not value:
        raise HTTPException(status_code=400, detail="field and value are required")

    datum = CollectedDatum(
        field=field,
        value=value,
        conversation_id=payload.conversation_id,
        campaign=payload.campaign,
    )
    session.add(datum)
    session.commit()
    session.refresh(datum)
    return {"saved": True, "id": datum.id}
