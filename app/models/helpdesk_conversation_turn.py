"""Durable event receipt and delivery intent for the conversational helpdesk."""

from datetime import datetime

from sqlmodel import Field, SQLModel


class HelpdeskConversationTurn(SQLModel, table=True):
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    candidate_message_id: str
    action_id: str = Field(index=True)
    # pending -> sending -> sent/drafted; uncertain sends require reconciliation.
    delivery_status: str = "pending"
    delivery_mode: str = "dry_run"
    remote_reply_id: str | None = None
    recipient: str | None = None
    sender: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
