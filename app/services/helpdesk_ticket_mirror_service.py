from datetime import datetime
from sqlmodel import Session, select

from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror


def upsert_ticket_mirror(session: Session, ticket: dict) -> HelpdeskTicketMirror:
    zoho_ticket_id = str(ticket["id"])

    mirror = session.exec(
        select(HelpdeskTicketMirror).where(
            HelpdeskTicketMirror.zoho_ticket_id == zoho_ticket_id
        )
    ).first()

    if mirror is None:
        mirror = HelpdeskTicketMirror(zoho_ticket_id=zoho_ticket_id, channel=ticket.get("channel", "Email"))

    contact = ticket.get("contact") or {}
    cf = ticket.get("cf") or {}

    created_time = ticket.get("createdTime")
    if created_time:
      mirror.ticket_created_at = datetime.fromisoformat(created_time)


    mirror.channel = ticket.get("channel") or mirror.channel
    mirror.ticket_number = ticket.get("ticketNumber")
    mirror.subject = ticket.get("subject")
    mirror.candidate_name = " ".join(
        part for part in [contact.get("firstName"), contact.get("lastName")] if part
    ) or None
    mirror.candidate_email = ticket.get("email") or contact.get("email")
    mirror.candidate_phone = ticket.get("phone") or contact.get("mobile")
    mirror.campaign_name = cf.get("cf_campaign_name")
    mirror.issue_category = cf.get("cf_enquiry_type")
    mirror.sentiment = ticket.get("sentiment")
    mirror.zoho_status = ticket.get("status")
    mirror.zoho_assignee_id = ticket.get("assigneeId")
    mirror.zoho_department_id = ticket.get("departmentId")
    mirror.zoho_web_url = ticket.get("webUrl")
    mirror.last_synced_at = datetime.utcnow()
    mirror.updated_at = datetime.utcnow()

    session.add(mirror)
    return mirror



def sync_ticket_mirror_from_zoho(session: Session, client, department_id: str | None = None, limit: int = 50) -> dict:
    tickets = client.list_tickets(department_id=department_id, limit=limit)
    synced = 0
    for ticket in tickets.get("data", []):
        upsert_ticket_mirror(session, ticket)
        synced += 1
    session.commit()
    return {"synced": synced}

