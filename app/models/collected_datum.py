"""A piece of information a caller gave the agent during a call — confirmed via
echo-back and saved. This is ONLY ever what the caller provided (e.g. an email
they spelled out), never a value looked up from our records.
"""

from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class CollectedDatum(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str | None = Field(default=None, index=True)
    campaign: str | None = Field(default=None, index=True)
    field: str          # what was collected, e.g. "email"
    value: str          # the caller-provided, echo-back-confirmed value
    created_at: datetime = Field(default_factory=datetime.utcnow)
