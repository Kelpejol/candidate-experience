"""Response schemas for the Helpdesk review API."""

from datetime import datetime

from pydantic import BaseModel


class HelpdeskAIActionRead(BaseModel):
    """One AI decision, enriched with ticket context, for the review UI."""

    id: str
    zoho_ticket_id: str
    subject: str | None = None
    candidate_email: str | None = None
    channel: str | None = None
    action_type: str
    rule: str
    issue_category: str | None = None
    confidence_label: str | None = None
    sensitivity_detected: bool = False
    grounding_status: str | None = None
    draft_text: str | None = None
    reason: str | None = None
    executed: bool = False
    created_at: datetime
