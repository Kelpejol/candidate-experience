"""Twilio voice call routes for handing a call off to a human agent.

Provides the TwiML endpoint Twilio requests when a call needs to be
bridged to a live agent (with a hold message and dial-out), and the
status callback Twilio hits with the outcome of that dial so it can be
recorded against the originating call record.
"""

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

    Called by Twilio (or the voice agent) mid-call. Builds and returns
    TwiML that plays a hold message and dials the configured handoff
    phone number, with a status callback URL (carrying
    ``external_call_id``) that Twilio will POST to once the dial
    completes. No database access here.
    """
    # external_call_id is threaded through as a query param so the status
    # callback below can look the call record back up.
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
    """
    Twilio status callback for a call handoff's ``<Dial>`` outcome.

    Invoked by Twilio (via the ``status_callback_url`` set in
    ``handoff_call``) with the result of dialing the human agent. The
    form fields use Twilio's ``Dial*``/``RecordingUrl`` parameter names
    (mapped here via ``Form(alias=...)``). Looks up the call record by
    ``external_call_id`` and persists the handoff outcome. Raises 404
    if the call record does not exist.
    """
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