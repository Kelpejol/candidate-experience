"""List recent Zoho Desk tickets, or inspect one ticket in detail.

Usage:
    python scripts/check_zoho_tickets.py               # recent tickets
    python scripts/check_zoho_tickets.py <ticket_id>   # one ticket + threads
"""

import sys

from app.core.config import get_settings
from app.integrations.zoho_desk_client import build_zoho_desk_client


settings = get_settings()

if not settings.zoho_org_id:
    raise RuntimeError("ZOHO_ORG_ID is not configured (run check_zoho_auth.py first)")

client = build_zoho_desk_client(settings)

if len(sys.argv) > 1:
    ticket_id = sys.argv[1]

    ticket = client.get_ticket(ticket_id)
    print(
        f"#{ticket.get('ticketNumber')} - "
        f"{ticket.get('subject')} - "
        f"status: {ticket.get('status')} - "
        f"channel: {ticket.get('channel')} - "
        f"email: {ticket.get('email')}"
    )

    threads = client.list_ticket_threads(ticket_id)
    for thread in threads.get("data", []):
        print(
            f"  thread {thread.get('id')} - "
            f"{thread.get('channel')} - "
            f"{thread.get('direction')} - "
            f"{thread.get('createdTime')} - "
            f"{(thread.get('summary') or '')[:80]}"
        )
else:
    tickets = client.list_tickets(department_id=settings.zoho_department_id, limit=20)
    for ticket in tickets.get("data", []):
        print(
            f"{ticket.get('id')} - "
            f"#{ticket.get('ticketNumber')} - "
            f"{ticket.get('channel')} - "
            f"{ticket.get('status')} - "
            f"{ticket.get('subject')}"
        )
