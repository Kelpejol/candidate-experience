"""Webhook endpoints for the ElevenLabs conversational voice agent.

Handles call-ended notifications, a raw/debug passthrough, and the
signature-verified ElevenLabs webhook (post-call transcription) used
to persist finished outbound/inbound calls as call records.
"""

import logging

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlmodel import Session

from elevenlabs import ElevenLabs, BadRequestError

from app.core.config import get_settings

from app.core.database import get_session
from app.schemas.call_record import CallRecordCreate, CallRecordResponse
from app.services.call_record_service import create_call_record, get_call_record_by_external_id
from app.services.elevenlabs_webhook_mapper import map_post_call_transcription_to_call_record
from app.services.outbound_call_execution_service import apply_outbound_call_webhook_result
from app.services.csat_service import create_csat_invitation_for_call, is_call_csat_eligible


router = APIRouter(prefix="/webhooks/voice-agent", tags=["Voice Agent Webhooks"])


@router.post("/call-ended", response_model=CallRecordResponse)
def voice_agent_call_ended(
    payload: CallRecordCreate,
    session: Session = Depends(get_session),
):
    """
    Webhook endpoint to handle call ended events from the voice agent.

    Idempotent: if a call record with this ``external_call_id`` already
    exists (e.g. the webhook was retried), returns the existing record
    instead of creating a duplicate. Otherwise persists a new call
    record.
    """
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
    """Debug endpoint that logs the raw JSON body of any voice agent webhook.

    Does not validate, verify, or persist anything - it just prints the
    payload for inspection and acknowledges receipt. Intended for
    diagnosing/inspecting unfamiliar webhook payloads during integration.
    """
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
    """
    Verified webhook endpoint for ElevenLabs conversational agent events.

    Verifies the ``elevenlabs-signature`` header against the raw request
    body using the configured webhook secret (via the ElevenLabs SDK's
    ``construct_event``), rejecting the request with 401 if verification
    fails. This must use the raw body (not a re-serialized JSON parse)
    since the signature is computed over the exact bytes sent. Only
    ``post_call_transcription`` events are processed; other event types
    are acknowledged but ignored. On a valid transcription event, maps
    the payload to a call record and persists it, but is idempotent -
    if a record for this ``external_call_id`` already exists (e.g. a
    webhook retry), it is not duplicated.

    Raises 500 if the webhook secret is not configured, and 401 on
    signature verification failure.
    """
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
        # construct_event raises BadRequestError for a missing/invalid
        # signature (or a stale timestamp) - treat as unauthorized.
        raise HTTPException(
            status_code=401,
            detail="Invalid ElevenLabs webhook signature",
        ) from exc

    if payload.get("type") != "post_call_transcription":
        # ElevenLabs sends other event types too; we only care about the
        # final post-call transcription, so acknowledge and skip the rest.
        return {
            "received": True,
            "message": "Not a post call transcription event",
            "stored": False,
        }

    # The payload is already signature-verified at this point, so a mapping
    # failure means a shape we didn't anticipate — not an attack. Returning 200
    # stops the provider retrying a payload that will never map, while the log
    # keeps the evidence. A 500 here would also skip the outbound-attempt
    # update below, stranding that attempt in "calling" until the sweep.
    try:
        call_record_payload = map_post_call_transcription_to_call_record(payload)
    except Exception:
        logging.exception("Could not map a post-call webhook; acknowledging")
        return {
            "received": True,
            "message": "Payload could not be mapped",
            "stored": False,
        }

    existing_record = get_call_record_by_external_id(
        call_record_payload.external_call_id,
        session,
    )

    if existing_record:
        # Duplicate delivery (providers retry), or a retry after a crash that
        # created the CallRecord but hadn't yet closed the outbound loop. Re-run
        # the outbound apply so an attempt left in "calling" by a partial first
        # delivery still gets closed — apply_* is idempotent and preserves
        # terminal opt-out/completion, so repeating it is safe.
        apply_outbound_call_webhook_result(session, payload)
        return {
            "received": True,
            "message": "Call record already exists",
            "stored": False,
            "external_call_id": existing_record.external_call_id,
        }

    call_record = create_call_record(call_record_payload, session)

    # For an outbound campaign call, also close the loop: update the
    # originating OutboundCallAttempt and the candidate with this outcome.
    # Returns None for inbound calls (or an unknown/stale attempt).
    updated_attempt = apply_outbound_call_webhook_result(session, payload)

    # For an inbound support call (no outbound attempt), auto-create a CSAT
    # invitation when the flag is on and the call is CSAT-eligible. Creation
    # is idempotent per call and only resolves contact/status here; the send
    # stays behind the gated send-pending job.
    csat_created = False
    if (
        updated_attempt is None
        and settings.csat_auto_create
        and is_call_csat_eligible(call_record)
    ):
        create_csat_invitation_for_call(session=session, call_record=call_record)
        csat_created = True

    return {
        "received": True,
        "message": "Call record created successfully",
        "stored": True,
        "external_call_id": call_record.external_call_id,
        "direction": call_record.direction,
        "outbound_attempt_updated": updated_attempt.id if updated_attempt else None,
        "csat_invitation_created": csat_created,
    }