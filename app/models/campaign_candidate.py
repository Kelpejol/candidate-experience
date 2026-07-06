

from datetime import datetime
from uuid import uuid4

from sqlmodel import SQLModel, Field


class CampaignCandidate(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    campaign_id: str =Field(index=True)

    candidate_name: str | None = Field(default=None)
    email: str | None = Field(default=None, index=True)
    phone: str | None = Field(default=None, index=True)

    tool_name: str | None = Field(default=None, index=True)
    campaign_name: str | None = Field(default=None)
    external_candidate_id: str | None = Field(default=None, index=True)

    surveymonkey_recipient_id: str | None = Field(default=None, index=True)
    surveymonkey_response_id: str | None = Field(default=None, index=True)
    surveymonkey_response_status: str | None = Field(default=None)
    survey_responded_at: datetime | None = Field(default=None)

    survey_status: str = Field(default="not_sent", index=True)
    call_status: str = Field(default="not_queued", index=True)

    opted_out_call: bool = Field(default=False)
    opted_out_email: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)