"""sync_ticket_mirror_from_zoho: paginating past the first page, and
reconciling a mirror that's fallen off the Open list.

A single-page fetch used to miss every Open ticket beyond the first `limit`
permanently, and a ticket that closed after dropping out of that page stayed
pinned at "Open" in our mirror forever — so the pipeline kept "processing" a
ticket that had been resolved weeks earlier.
"""

from sqlmodel import select

from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ticket_mirror_service import sync_ticket_mirror_from_zoho


def _ticket(ticket_id, status="Open"):
    return {"id": ticket_id, "channel": "Email", "status": status}


class _FakeClient:
    def __init__(self, pages, tickets_by_id=None):
        self.pages = pages  # list of lists of ticket dicts, one per from_index step
        self.tickets_by_id = tickets_by_id or {}
        self.get_ticket_calls = []

    def list_tickets(self, department_id=None, status=None, limit=50, from_index=0):
        page_index = from_index // limit
        return {"data": self.pages[page_index] if page_index < len(self.pages) else []}

    def get_ticket(self, ticket_id):
        self.get_ticket_calls.append(ticket_id)
        return self.tickets_by_id[ticket_id]


def test_pages_past_the_first_page(session):
    page1 = [_ticket(str(i)) for i in range(3)]
    page2 = [_ticket(str(i)) for i in range(3, 5)]
    client = _FakeClient(pages=[page1, page2])

    result = sync_ticket_mirror_from_zoho(session, client, limit=3)

    assert result["synced"] == 5
    mirrored_ids = {
        m.zoho_ticket_id for m in session.exec(select(HelpdeskTicketMirror)).all()
    }
    assert mirrored_ids == {"0", "1", "2", "3", "4"}


def test_a_ticket_that_dropped_off_the_open_list_is_reconciled(session):
    # First sync: one open ticket.
    client = _FakeClient(pages=[[_ticket("1")]])
    sync_ticket_mirror_from_zoho(session, client, limit=50)
    mirror = session.exec(select(HelpdeskTicketMirror)).first()
    assert mirror.zoho_status == "Open"

    # Second sync: the ticket closed and no longer appears in the Open list.
    client2 = _FakeClient(
        pages=[[]],  # nothing Open anymore
        tickets_by_id={"1": _ticket("1", status="Closed")},
    )
    result = sync_ticket_mirror_from_zoho(session, client2, limit=50)

    assert result["reconciled"] == 1
    assert client2.get_ticket_calls == ["1"]
    session.refresh(mirror)
    assert mirror.zoho_status == "Closed"


def test_a_ticket_still_open_is_not_reconciled_twice(session):
    client = _FakeClient(pages=[[_ticket("1")]])
    result = sync_ticket_mirror_from_zoho(session, client, limit=50)
    assert result["synced"] == 1
    assert result["reconciled"] == 0
