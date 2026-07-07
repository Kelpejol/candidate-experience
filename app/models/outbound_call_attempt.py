from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class OutboundCallAttempt(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    campaign_id: str = Field(index=True)
    candidate_id: str = Field(index=True)

    candidate_name: str | None = Field(default=None)
    candidate_email: str | None = Field(default=None, index=True)
    campaign_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)

    phone: str = Field(index=True)
    attempt_number: int = Field(default=1)

    status: str = Field(default="queued", index=True)
    disposition: str | None = Field(default=None)

    elevenlabs_conversation_id: str | None = Field(default=None, index=True)
    transcript: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)

    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)