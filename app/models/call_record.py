from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class CallRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    external_call_id: str = Field(index=True, unique=True)
    direction: str
    candidate_phone: str | None = Field(default=None)
    candidate_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    campaign_name: str | None = Field(default=None)
    disposition: str
    issue_summary: str | None = Field(default=None)
    transcription: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)
    handoff_call_sid: str | None = Field(default=None)
    handoff_status: str | None = Field(default=None)
    handoff_duration: int | None = Field(default=None)
    handoff_bridged: bool | None = Field(default=None)
    call_start_time: datetime | None = Field(default=None)
    call_end_time: datetime | None = Field(default=None)
    

    created_at: datetime = Field(default_factory=datetime.utcnow)