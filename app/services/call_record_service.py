"""Data-access layer for CallRecord entities (inbound/outbound call logs).

Provides CRUD-style helpers used by the API routes and webhook handlers to
look up, list, create, and update the status of call records, including the
handoff-to-human sub-flow.
"""

from sqlmodel import Session, select

from app.models.call_record import CallRecord
from app.schemas.call_record import CallRecordCreate


def get_call_record_by_external_id(external_call_id: str, session: Session):
    """
    Retrieve a call record by its external_call_id.
    """
    return session.exec(
        select(CallRecord).where(CallRecord.external_call_id == external_call_id)
    ).first()


def list_call_records(
    session: Session,
    direction: str | None = None,
    disposition: str | None = None,
    tool_name: str | None = None,
    campaign_name: str | None = None,
    limit: int = 50,
    offset: int = 0,

):
    """
    Retrieve call records, optionally filtered by direction, disposition,
    tool_name, and/or campaign_name, ordered newest-first and paginated
    with limit/offset.
    """
    statement = select(CallRecord)
    if direction:
        statement = statement.where(CallRecord.direction == direction)
    if disposition:
        statement = statement.where(CallRecord.disposition == disposition)
    if tool_name:
        statement = statement.where(CallRecord.tool_name == tool_name)
    if campaign_name:
        statement = statement.where(CallRecord.campaign_name == campaign_name)
    statement = (statement.order_by(CallRecord.created_at.desc()).limit(limit).offset(offset))
    return session.exec(statement).all()


def create_call_record(call_record_data: CallRecordCreate, session: Session):
    """
    Create a new call record from the given data.

    Side effects: persists the new CallRecord to the DB (add/commit/refresh).
    """
    db_call_record = CallRecord(
        external_call_id=call_record_data.external_call_id,
        direction=call_record_data.direction,
        candidate_phone=call_record_data.candidate_phone,
        candidate_name=call_record_data.candidate_name,
        tool_name=call_record_data.tool_name,
        campaign_name=call_record_data.campaign_name,
        disposition=call_record_data.disposition,
        issue_summary=call_record_data.issue_summary,
        transcription=call_record_data.transcription,
        recording_url=call_record_data.recording_url,
        call_start_time=call_record_data.call_start_time,
        call_end_time=call_record_data.call_end_time,
    )
    session.add(db_call_record)
    session.commit()
    session.refresh(db_call_record)

    return db_call_record


def update_call_record_handoff_status(
    call_record: CallRecord,
    session: Session,
    dial_call_sid: str | None = None,
    dial_call_status: str | None = None,
    dial_call_duration: str | None = None,
    dial_bridged: str | None = None,
    recording_url: str | None = None,
):
    """
    Update a call record with the outcome of a Twilio <Dial> handoff to a
    human agent (call sid/status/duration/bridged flag, recording URL) and
    derive the record's disposition from the dial status.

    Params mirror Twilio's DialCallStatus/DialCallSid/DialCallDuration/
    DialBridged callback fields (passed in as strings, per Twilio's webhook
    payload format).

    Side effects: persists the changes to the DB (add/commit/refresh).
    """
    call_record.handoff_call_sid = dial_call_sid
    call_record.handoff_status = dial_call_status

    call_record.recording_url = recording_url

    # Twilio sends duration/bridged as strings; convert to native types.
    if dial_call_duration is not None:
        call_record.handoff_duration = int(dial_call_duration)
    if dial_bridged is not None:
        call_record.handoff_bridged = dial_bridged.lower() == "true"
    if dial_call_status == "completed":
        call_record.disposition = "handed_off_to_human"
    if dial_call_status in {"failed", "busy", "no-answer"}:
        call_record.disposition = "handoff_failed"

    session.add(call_record)
    session.commit()
    session.refresh(call_record)

    return call_record    