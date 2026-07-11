from collections import Counter

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine, create_db_and_tables
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import process_ticket

create_db_and_tables()
zoho = build_zoho_desk_client(get_settings())
tally = Counter()

with Session(engine) as session:
    mirrors = session.exec(
        select(HelpdeskTicketMirror)
        .where(HelpdeskTicketMirror.zoho_status == "Open")
        .order_by(HelpdeskTicketMirror.ticket_created_at.desc())
        .limit(10)
    ).all()

    for mirror in mirrors:
        action = process_ticket(session, zoho, mirror)
        tally[action.action_type] += 1
        grounding = f" kb={action.grounding_status}" if action.grounding_status else ""
        drafted = " +draft" if action.draft_text else ""
        print(f"#{mirror.ticket_number} [{action.action_type}] ({action.rule}){grounding}{drafted} {mirror.subject}")
    session.commit()

print(f"\nSummary: {dict(tally)}")
