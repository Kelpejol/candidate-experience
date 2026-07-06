

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlmodel import Session

from elevenlabs import ElevenLabs, BadRequestError

from app.core.config import get_settings

from app.core.database import get_session
from app.schemas.call_record import CallRecordCreate, CallRecordResponse
from app.services.call_record_service import create_call_record, get_call_record_by_external_id
from app.services.elevenlabs_webhook_mapper import map_post_call_transcription_to_call_record


router = APIRouter(prefix="/webhooks/voice-agent", tags=["Voice Agent Webhooks"])


@router.post("/call-ended", response_model=CallRecordResponse)
def voice_agent_call_ended(
    payload: CallRecordCreate,
    session: Session = Depends(get_session),
):
    """
    Webhook endpoint to handle call ended events from the voice agent."""
    existing_record = get_call_record_by_external_id(
        payload.external_call_id,
        session,
    )


    if existing_record:
        return CallRecordResponse(
            message="Voice agent call record already exists",
            accepted=True,
            external_call_id=existing_record.external_call_id,
        )
    call_record = create_call_record(payload, session)

    return CallRecordResponse(
        message="Voice agent call record created successfully",
        accepted=True,
        external_call_id=call_record.external_call_id,
    )



@router.post("/raw")
async def voice_agent_raw_webhook(request: Request):
    payload = await request.json()
    print("VOICE_AGENT_RAW_WEBHOOK:", payload)

    return {
        "received": True,
    }


@router.post("/elevenlabs")
async def elevenlabs_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    settings = get_settings()
    raw_body = await request.body()
    signature = request.headers.get("elevenlabs-signature")

    if not settings.elevenlabs_webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="ElevenLabs webhook secret is not configured",
        )

    client = ElevenLabs(api_key=settings.elevenlabs_api_key or "")

    try:
        payload = client.webhooks.construct_event(
            rawBody=raw_body.decode("utf-8"),
            sig_header=signature or "",
            secret=settings.elevenlabs_webhook_secret,
        )
    except BadRequestError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid ElevenLabs webhook signature",
        ) from exc

    if payload.get("type") != "post_call_transcription":
        return {
            "received": True,
            "message": "Not a post call transcription event",
            "stored": False,
        }

    call_record_payload = map_post_call_transcription_to_call_record(payload)

    existing_record = get_call_record_by_external_id(
        call_record_payload.external_call_id,
        session,
    )

    if existing_record:
        return {
            "received": True,
            "message": "Call record already exists",
            "stored": False,
            "external_call_id": existing_record.external_call_id,
        }

    call_record = create_call_record(call_record_payload, session)

    return {
        "received": True,
        "message": "Call record created successfully",
        "stored": True,
        "external_call_id": call_record.external_call_id,
    }