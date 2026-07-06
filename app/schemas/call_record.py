from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal

from app.core.vocabulary import is_tool_allowed


CallDirection = Literal["inbound", "outbound"]
CallDisposition = Literal[
    "answered_by_ai", 
    "handed_off_to_human",
    "handoff_failed",
    "missed", 
    "voicemail",
    "no_answer",
    "opted_out", 
    "failed"
    ]

class CallRecordRead(BaseModel):
    id: str
    external_call_id: str
    direction: str
    candidate_phone: str | None = None
    candidate_name: str | None = None
    tool_name: str | None = None
    campaign_name: str | None = None
    disposition: str
    issue_summary: str | None = None
    transcription: str | None = None
    recording_url: str | None = None
    call_start_time: datetime | None = None
    call_end_time: datetime | None = None
    created_at: datetime
    handoff_call_sid: str | None = None
    handoff_status: str | None = None
    handoff_duration: int | None = None
    handoff_bridged: bool | None = None

class CallRecordCreate(BaseModel):
    external_call_id: str = Field(min_length=1)
    direction: CallDirection 
    candidate_phone: str | None = Field(default=None)
    candidate_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    campaign_name: str | None = Field(default=None)
    disposition: CallDisposition
    issue_summary: str | None = Field(default=None)
    transcription: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)
    handoff_call_sid: str | None = Field(default=None)
    handoff_status: str | None = Field(default=None)
    handoff_duration: int | None = Field(default=None)
    handoff_bridged: bool | None = Field(default=None)
    call_start_time: datetime | None = Field(default=None)
    call_end_time: datetime | None = Field(default=None)

    @field_validator("tool_name")
    @classmethod
    def tool_name_must_be_allowed(cls, v: str | None):
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v    

    @field_validator("external_call_id", "direction", "disposition")
    @classmethod
    def required_string_must_not_be_empty(cls, v: str):
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v


class CallRecordResponse(BaseModel):
    accepted: bool
    external_call_id: str
    message: str