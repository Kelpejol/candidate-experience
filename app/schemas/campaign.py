
from pydantic import BaseModel, Field, field_validator, EmailStr

from datetime import datetime

from typing import Literal

from app.core.vocabulary import is_tool_allowed


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
    name: str = Field(min_length=1)
    tool_name: str | None = None
    survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    response_wait_hours: int = Field(default=24, ge=1, le=336)

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str):
        if not v.strip():
            raise ValueError("Campaign name must not be empty")
        return v
    
    @field_validator("tool_name")
    @classmethod
    def tool_name_must_be_allowed(cls, v: str | None):
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v
    


class CampaignRead(BaseModel):
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




class CampaignCandidateCreate(BaseModel):
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
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v
    
    @field_validator("phone")
    @classmethod
    def phone_must_not_be_empty(cls, v: str | None):
        if v is not None and not v.strip():
            raise ValueError("Phone must not be empty")
        return v
    


class CampaignCandidateRead(BaseModel):
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


class CampaignSurveySyncResponse(BaseModel):
    total_recipients: int
    completed: int
    partial: int
    non_responders: int
    updated_candidates: int