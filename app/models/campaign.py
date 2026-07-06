
from datetime import datetime

from sqlmodel import Field, SQLModel
from uuid import uuid4


class Campaign(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    
    name: str = Field(index=True)
    tool_name: str | None = Field(default=None, index=True)

    survey_id: str | None = Field(default=None)
    surveymonkey_collector_id: str | None = Field(default=None)

    status: str = Field(default="draft", index=True)
    response_wait_hours: int = Field(default=24)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    survey_sent_at: datetime | None = Field(default=None)
    non_responder_checked_at: datetime | None = Field(default=None)