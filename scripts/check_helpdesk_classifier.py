from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import get_latest_candidate_message
from app.services.helpdesk_classifier import classify_ticket

client = build_zoho_desk_client(get_settings())

with Session(engine) as session:
    mirrors = session.exec(
        select(HelpdeskTicketMirror).order_by(HelpdeskTicketMirror.ticket_created_at.desc()).limit(5)
    ).all()

for mirror in mirrors:
    body = get_latest_candidate_message(client, mirror.zoho_ticket_id)

    result = classify_ticket(subject=mirror.subject or "", body=body)
    print(f"#{mirror.ticket_number} {mirror.subject}")
    print(f"   -> {result.issue_category} | tool={result.tool_name} | "
          f"campaign={result.campaign_name} | sensitive={result.sensitivity_detected} | "
          f"{result.confidence_label} | {result.reason}\n")
