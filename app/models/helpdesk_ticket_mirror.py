from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class HelpdeskTicketMirror(SQLModel, table=True):

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    # Zoho's ticket id — unique so webhook events can upsert instead of duplicating
    zoho_ticket_id: str = Field(index=True, unique=True)
    ticket_number: str | None = Field(default=None)
    channel: str  # "Email" | "WhatsApp" (Zoho's channel value)
    subject: str | None = Field(default=None)

    candidate_name: str | None = Field(default=None)
    candidate_email: str | None = Field(default=None, index=True)
    candidate_phone: str | None = Field(default=None)

    # classification — mirrors Zoho custom fields + our controlled vocabulary
    tool_name: str | None = Field(default=None)
    campaign_name: str | None = Field(default=None)
    issue_category: str | None = Field(default=None)
    sentiment: str | None = Field(default=None)

    # our AI's view of this ticket
    ai_disposition: str | None = Field(default=None)  # auto_replied | drafted | routed_to_human | no_action

    # Zoho's view of this ticket
    zoho_status: str | None = Field(default=None)
    zoho_assignee_id: str | None = Field(default=None)
    zoho_department_id: str | None = Field(default=None)
    zoho_web_url: str | None = Field(default=None)

    ticket_created_at: datetime | None = Field(default=None)
    last_synced_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
