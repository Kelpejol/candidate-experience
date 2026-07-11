from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class HelpdeskAIAction(SQLModel, table=True):
    """One row per AI decision about a ticket — the audit trail.

    Dry-run rows have executed=False: the decision was recorded but no
    Zoho action (draft/assignment/tag) was performed.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    zoho_ticket_id: str = Field(index=True)

    action_type: str            # draft_reply | route_to_human | tag_only
    rule: str                   # which decision rule fired
    issue_category: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    campaign_name: str | None = Field(default=None)
    confidence_label: str | None = Field(default=None)
    sensitivity_detected: bool = Field(default=False)
    grounding_status: str | None = Field(default=None)   # filled once the KB exists
    draft_text: str | None = Field(default=None)          # filled by the draft part
    reason: str | None = Field(default=None)
    executed: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
