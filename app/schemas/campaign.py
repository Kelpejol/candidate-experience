"""Pydantic schemas for campaigns, campaign candidates, and outbound call attempts.

Covers the request/response shapes used by the campaigns API: creating and
reading campaigns, uploading/reading candidates, updating survey/call
status, and reading outbound call attempts and survey sync summaries.
"""

from pydantic import BaseModel, Field, field_validator, EmailStr

from datetime import datetime

from typing import Literal

from app.core.vocabulary import is_tool_allowed


# Campaign lifecycle: draft -> uploaded -> survey_sending/sent -> waiting_for_responses
# -> non_response_checking -> outbound_ready -> outbound_calling -> completed/failed.
CampaignStatus = Literal[
   "draft",
    "uploaded",
    "survey_sending",
    "survey_sent",
    "waiting_for_responses",
    "non_response_checking",
    "outbound_ready",
    "outbound_calling",
    "completed",
    "failed",
]

SurveyStatus = Literal[
    "not_sent",
    "sent",
    "responded",
    "partial_response",
    "non_responder",
    "excluded_opt_out",
    "failed"
]

CallStatus = Literal[
    "not_queued",
    "queued",
    "calling",
    "answered",
    "responded_by_call",
    "no_answer",
    "busy",
    "voicemail",
    "failed",
    "opted_out",
    "handed_off_to_human",
]



class CampaignCreate(BaseModel):
    """Request shape for creating a new campaign.

    `response_wait_hours` bounds how long the system waits for survey
    responses before treating a candidate as a non-responder (1 to 336
    hours, default 24). `tool_name`, when provided, must be a recognized
    tool per app.core.vocabulary.is_tool_allowed.
    """

    name: str = Field(min_length=1)
    tool_name: str | None = None
    survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    response_wait_hours: int = Field(default=24, ge=1, le=336)

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str):
        """Reject a name that is blank or whitespace-only."""
        if not v.strip():
            raise ValueError("Campaign name must not be empty")
        return v
    
    @field_validator("tool_name")
    @classmethod
    def tool_name_must_be_allowed(cls, v: str | None):
        """Reject tool_name values outside app.core.vocabulary.ALLOWED_TOOLS. None is allowed (field is optional)."""
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v
    


class CampaignRead(BaseModel):
    """Response shape for a persisted campaign, including lifecycle status and timestamps."""

    id: str
    name: str
    tool_name: str | None = None
    survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    status: CampaignStatus
    response_wait_hours: int
    created_at: datetime
    survey_sent_at: datetime | None = None
    non_responder_checked_at: datetime | None = None


class CampaignStatusUpdate(BaseModel):
    """Request shape for transitioning a campaign to a new CampaignStatus."""

    status: CampaignStatus


class CampaignSummaryRead(BaseModel):
    """Response shape aggregating a campaign's candidate counts by survey status, call status, and outbound attempt outcome."""

    campaign_id: str
    total_candidates: int
    survey_statuses: dict[str, int]
    call_statuses: dict[str, int]
    outbound_attempts: dict[str, int]


class CampaignCandidateCreate(BaseModel):
    """Request shape for adding/uploading a candidate to a campaign.

    All fields besides the opt-out flags are optional to accommodate
    partial candidate data from uploads. `tool_name`, when provided, must
    be a recognized tool per app.core.vocabulary.is_tool_allowed.
    """

    candidate_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    tool_name: str | None = None
    campaign_name: str | None = None
    external_candidate_id: str | None = None
    opted_out_call: bool = False
    opted_out_email: bool = False


    @field_validator("tool_name")
    @classmethod
    def candidate_tool_name_must_be_allowed(cls, v: str | None):
        """Reject tool_name values outside app.core.vocabulary.ALLOWED_TOOLS. None is allowed (field is optional)."""
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v
    
    @field_validator("phone")
    @classmethod
    def phone_must_not_be_empty(cls, v: str | None):
        """Reject a phone value that is present but blank/whitespace-only. None is allowed (field is optional)."""
        if v is not None and not v.strip():
            raise ValueError("Phone must not be empty")
        return v
    


class CampaignCandidateRead(BaseModel):
    """Response shape for a persisted campaign candidate, including survey and call progress."""

    id: str
    campaign_id: str
    candidate_name: str | None = None
    email: str | None = None
    phone: str | None = None
    tool_name: str | None = None
    campaign_name: str | None = None
    external_candidate_id: str | None = None
    surveymonkey_recipient_id: str | None = None
    surveymonkey_response_id: str | None = None
    surveymonkey_response_status: str | None = None
    survey_responded_at: datetime | None = None
    survey_status: SurveyStatus
    call_status: CallStatus
    opted_out_call: bool
    opted_out_email: bool
    created_at: datetime
    updated_at: datetime




class CampaignCandidateSurveyStatusUpdate(BaseModel):
    """Request shape for setting a candidate's survey status directly."""

    survey_status: SurveyStatus




OutboundCallAttemptStatus = Literal[
    "queued",
    "calling",
    "answered",
    "responded_by_call",
    "no_answer",
    "busy",
    "voicemail",
    "failed",
    "opted_out",
    "handed_off_to_human",
]


class OutboundCallAttemptRead(BaseModel):
    """Response shape for a single outbound call attempt against a candidate, including transcript/recording once available."""

    id: str
    campaign_id: str
    candidate_id: str
    candidate_name: str | None = None
    candidate_email: str | None = None
    campaign_name: str | None = None
    tool_name: str | None = None
    phone: str
    attempt_number: int
    status: OutboundCallAttemptStatus
    disposition: str | None = None
    elevenlabs_conversation_id: str | None = None
    transcript: str | None = None
    summary: str | None = None
    recording_url: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime

class OutboundCallAttemptStatusUpdate(BaseModel):
    """Request shape for updating an outbound call attempt's status and outcome details, typically from a provider callback."""

    status: OutboundCallAttemptStatus
    disposition: str | None = None
    elevenlabs_conversation_id: str | None = None
    transcript: str | None = None
    summary: str | None = None
    recording_url: str | None = None

class CampaignSurveySyncResponse(BaseModel):
    """Response shape summarizing the outcome of a SurveyMonkey response sync for a campaign."""

    total_recipients: int
    completed: int
    partial: int
    non_responders: int
    updated_candidates: int