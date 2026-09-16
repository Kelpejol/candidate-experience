import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import get_session
from app.schemas.voice_kb import KbAnswerResponse, KbQueryRequest
from app.services.campaign_scope_service import resolve_campaign
from app.services.voice_kb_retrieval import retrieve_for_answer

router = APIRouter(prefix="/voice-agent/kb", tags=["Voice Agent KB"])


def require_tool_token(authorization: str | None = Header(default=None)):
    """Reject calls without the shared secret — but only once one is configured.

    Unset (dev) = open, so local runs/tests stay frictionless. Set in any
    environment where the endpoint is publicly reachable.
    """
    token = get_settings().voice_kb_tool_token
    if not token:
        return  # not configured -> open (dev only)
    # Accept the token with or without a "Bearer " prefix — the ElevenLabs tool
    # header is easy to set either way, and the prefix has caused repeated 401s.
    provided = (authorization or "").removeprefix("Bearer ").strip()
    if provided != token:
        raise HTTPException(status_code=401, detail="Invalid or missing tool token")


@router.post(
    "/query",
    response_model=KbAnswerResponse,
    dependencies=[Depends(require_tool_token)],
)
def query_kb(payload: KbQueryRequest, session: Session = Depends(get_session)):
    """Answer-or-escalate signal for one caller question, scoped to a campaign.

    If a campaign name is given, it's resolved to an active KB scope, PLUS
    that campaign's assessment-tool scope (FOT/Test Haven/Scholastica — never
    asked of the caller, resolved silently from the campaign) — so one query
    can match general OR tool-specific OR campaign-specific content. An
    unknown/inactive/ambiguous campaign falls back to general content only, so
    the caller still gets universal answers rather than nothing.
    """
    scope = None
    tool_scope = None
    if payload.campaign:
        resolved = resolve_campaign(session, payload.campaign)
        scope = resolved["scope"]            # None unless found+active
        tool_scope = resolved["tool_scope"]   # None unless found+active with a tool set

    # This runs live, mid-call. A gateway/vector-store blip must degrade to the
    # "escalate" signal (answer_available=False) — never a 500, which the agent
    # would surface as a broken tool. The caller gets handed off gracefully.
    try:
        return retrieve_for_answer(
            payload.question,
            k=payload.k,
            campaign=scope,
            extra_scopes=[tool_scope] if tool_scope else None,
        )
    except Exception:
        logging.exception("KB retrieval failed for a live call; escalating")
        return KbAnswerResponse(answer_available=False)
