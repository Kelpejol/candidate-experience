"""Response schema for CSAT invitations."""

from datetime import datetime

from pydantic import BaseModel


class CsatInvitationRead(BaseModel):
    """Read shape for a post-call CSAT invitation."""

    id: str
    call_record_id: str
    candidate_email: str | None = None
    candidate_phone: str | None = None
    surveymonkey_survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    surveymonkey_message_id: str | None = None
    surveymonkey_recipient_id: str | None = None
    surveymonkey_response_id: str | None = None
    status: str
    score: int | None = None
    comment: str | None = None
    sent_at: datetime | None = None
    responded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
