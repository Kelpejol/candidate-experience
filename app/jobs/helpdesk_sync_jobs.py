from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import engine
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.services.helpdesk_ticket_mirror_service import sync_ticket_mirror_from_zoho


def sync_helpdesk_ticket_mirror_job() -> dict:
    settings = get_settings()
    client = build_zoho_desk_client(settings)
    with Session(engine) as session:
        return sync_ticket_mirror_from_zoho(
            session=session,
            client=client,
            department_id=settings.zoho_department_id,
        )
