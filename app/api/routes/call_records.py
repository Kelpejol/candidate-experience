"""API routes for creating, listing, and retrieving call records.

Call records capture the outcome/metadata of a voice call (inbound or
outbound) against a candidate, keyed by an ``external_call_id`` supplied
by the calling system (e.g. the voice agent or telephony provider).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.call_record import CallDirection, CallDisposition, CallRecordCreate, CallRecordResponse, CallRecordRead
from app.services.call_record_service import (
    get_call_record_by_external_id,
    create_call_record as create_call_record_service,
    list_call_records as list_call_records_service)

router = APIRouter(prefix="/call-records", tags=["Call Records"])


@router.post("", response_model=CallRecordResponse, status_code=201, description="Create a new call record")
def create_call_record(call_record: CallRecordCreate, session=Depends(get_session)):
    """Create a new call record.

    Rejects the request with 409 if a record with the same
    ``external_call_id`` already exists (records are keyed uniquely by
    that id to keep creation idempotent). Persists the new record via
    ``create_call_record_service``.
    """
    existing_record = get_call_record_by_external_id(call_record.external_call_id, session)
    if existing_record:
        raise HTTPException(status_code=409, detail=f"A call record with this {call_record.external_call_id} already exists")

    new_record = create_call_record_service(call_record, session)

    return CallRecordResponse(
        message="Call record created successfully",
        accepted=True,
        external_call_id=new_record.external_call_id,
    )  

@router.get("", response_model=list[CallRecordRead], description="Get all call records")
def list_call_records(
    session: Session=Depends(get_session), 
    direction: CallDirection | None = None, 
    disposition: CallDisposition | None = None,
    tool_name: str | None = None,
    campaign_name: str | None = None,
    limit: int = Query(50, ge=1, le=100, description="Limit the number of records returned (1-100)"),
    offset: int = Query(0, ge=0, description="Offset for pagination (default is 0)")
):
    """List call records with optional filtering and pagination.

    Filters by direction, disposition, tool name, and/or campaign name
    when provided. Returns a paginated list of matching call records.
    """
    call_records = list_call_records_service(
        session,
        direction=direction, 
        disposition=disposition, 
        tool_name=tool_name,
        campaign_name=campaign_name,
        limit=limit, 
        offset=offset
    )
    return call_records


@router.get("/{external_call_id}", response_model=CallRecordRead, description="Get a call record by external_call_id")
def get_call_record(external_call_id: str, session: Session=Depends(get_session)):
    """Fetch a single call record by its external call id.

    Raises 404 if no matching call record exists.
    """
    call_record = get_call_record_by_external_id(external_call_id, session)
    if not call_record:
        raise HTTPException(status_code=404, detail="Call record not found")
    return call_record