from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Response
from sqlmodel import Session
from app.core.database import get_session
from app.services.twiml_service import build_handoff_twiml

from app.services.call_record_service import get_call_record_by_external_id, update_call_record_handoff_status

from app.core.config import get_settings




router = APIRouter(prefix="/voice", tags=["Voice"])




@router.post("/handoff")
def handoff_call(external_call_id: str):
    """
    Endpoint to handle call handoff to a human agent.
    """
    query_string = urlencode({"external_call_id": external_call_id})
    
    settings = get_settings()
    status_callback_url = f"{settings.public_base_url}/voice/handoff/status?{query_string}"
    twiml = build_handoff_twiml(hold_message=settings.handoff_hold_message, phone_number=settings.handoff_phone_number, status_callback_url=status_callback_url)
    return Response(content=twiml, media_type="application/xml")


@router.post("/handoff/status")
def handoff_status(
    external_call_id: str,
    dial_call_status: str | None = Form(default=None, alias="DialCallStatus"),
    dial_call_sid: str | None = Form(default=None, alias="DialCallSid"),
    dial_call_duration: str | None = Form(default=None, alias="DialCallDuration"),
    dial_bridged: str | None = Form(default=None, alias="DialBridged"),
    recording_url: str | None = Form(default=None, alias="RecordingUrl"),
    session: Session = Depends(get_session)
):
    call_record = get_call_record_by_external_id(external_call_id, session)


    if not call_record:
        raise HTTPException(status_code=404, detail="Call record not found")
    
    updated_call_record = update_call_record_handoff_status(
        call_record, 
        session, 
        dial_call_sid, 
        dial_call_status, 
        dial_call_duration, 
        dial_bridged, 
        recording_url
    )

    return {
        "received": True,
        "external_call_id": updated_call_record.external_call_id,
        "dial_call_status": updated_call_record.handoff_status,
        "dial_call_sid": updated_call_record.handoff_call_sid,
        "dial_call_duration": updated_call_record.handoff_duration,
        "dial_bridged": updated_call_record.handoff_bridged,
        "recording_url": updated_call_record.recording_url,
    }