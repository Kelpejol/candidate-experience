"""Voice-agent tools for the OUTBOUND survey agent.

Separate from the inbound `/voice-agent/kb|campaign|collect` tools: this agent
doesn't answer questions, it asks them. It calls these endpoints with the
dynamic variables it was launched with (campaign_id, outbound_attempt_id, …).

Slice 1: serve a campaign's survey as spoken questions.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.routes.voice_kb import require_tool_token
from app.core.database import get_session
from app.schemas.outbound_survey import (
    OutboundAnswerRequest,
    OutboundAnswerResponse,
    OutboundOptOutRequest,
    OutboundOptOutResponse,
    OutboundQuestionsRequest,
    OutboundQuestionsResponse,
)
from app.services.outbound_survey_service import (
    get_campaign_survey_questions,
    record_opt_out,
    record_survey_answer,
)

router = APIRouter(prefix="/voice-agent/outbound", tags=["Voice Agent Outbound"])


@router.post(
    "/questions",
    response_model=OutboundQuestionsResponse,
    dependencies=[Depends(require_tool_token)],
)
def outbound_questions(
    payload: OutboundQuestionsRequest,
    session: Session = Depends(get_session),
):
    """Return the campaign's survey as an ordered list of spoken questions.

    404 if the campaign is unknown, 400 if it has no survey configured — the
    agent's script handles both by apologising and ending the call gracefully.
    """
    try:
        questions = get_campaign_survey_questions(payload.campaign_id, session)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return OutboundQuestionsResponse(
        campaign_id=payload.campaign_id,
        questions=questions,
    )


@router.post(
    "/answer",
    response_model=OutboundAnswerResponse,
    dependencies=[Depends(require_tool_token)],
)
def outbound_answer(
    payload: OutboundAnswerRequest,
    session: Session = Depends(get_session),
):
    """Record one survey answer the caller gave. Idempotent per question, so a
    correction replaces the earlier answer. 404 if the attempt is unknown."""
    try:
        row = record_survey_answer(
            session=session,
            campaign_id=payload.campaign_id,
            outbound_attempt_id=payload.outbound_attempt_id,
            question=payload.question,
            answer=payload.answer,
            position=payload.position,
            answer_type=payload.type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return OutboundAnswerResponse(saved=True, answer_id=row.id)


@router.post(
    "/opt-out",
    response_model=OutboundOptOutResponse,
    dependencies=[Depends(require_tool_token)],
)
def outbound_opt_out(
    payload: OutboundOptOutRequest,
    session: Session = Depends(get_session),
):
    """Honor "don't call me again" — terminal opt-out for this candidate. 404
    if the attempt is unknown."""
    try:
        record_opt_out(
            session=session,
            campaign_id=payload.campaign_id,
            outbound_attempt_id=payload.outbound_attempt_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return OutboundOptOutResponse(saved=True)
