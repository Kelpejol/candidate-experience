from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import engine, create_db_and_tables
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.services.helpdesk_ticket_mirror_service import upsert_ticket_mirror

settings = get_settings()
create_db_and_tables()
client = build_zoho_desk_client(settings)

tickets = client.list_tickets(department_id=settings.zoho_department_id, limit=50)

with Session(engine) as session:
    for ticket in tickets.get("data", []):
        mirror = upsert_ticket_mirror(session, ticket)
        print(f"#{mirror.ticket_number} - {mirror.channel} - {mirror.zoho_status} - {mirror.subject}")
    session.commit()
