from datetime import datetime
from sqlmodel import Session, select

from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror


def _parse_zoho_time(value: str | None) -> datetime | None:
    """Parse a Zoho timestamp, dropping the offset to match our naive columns.

    Returns None for a missing or unparseable value rather than raising — a
    malformed timestamp must not fail the whole sync.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def upsert_ticket_mirror(session: Session, ticket: dict) -> HelpdeskTicketMirror:
    zoho_ticket_id = str(ticket["id"])

    mirror = session.exec(
        select(HelpdeskTicketMirror).where(
            HelpdeskTicketMirror.zoho_ticket_id == zoho_ticket_id
        )
    ).first()

    if mirror is None:
        # `or` not a dict default: Zoho sends the key with a null value, and a
        # default only applies to a MISSING key — channel is NOT NULL, so a
        # present-but-null value would fail the insert.
        mirror = HelpdeskTicketMirror(
            zoho_ticket_id=zoho_ticket_id, channel=ticket.get("channel") or "Email"
        )

    contact = ticket.get("contact") or {}
    cf = ticket.get("cf") or {}

    created_time = ticket.get("createdTime")
    if created_time:
      mirror.ticket_created_at = _parse_zoho_time(created_time)

    # Zoho's own modification time — the real "did this ticket change?" signal.
    mirror.zoho_modified_at = (
        _parse_zoho_time(ticket.get("modifiedTime")) or mirror.zoho_modified_at
    )

    mirror.channel = ticket.get("channel") or mirror.channel
    mirror.ticket_number = ticket.get("ticketNumber")
    mirror.subject = ticket.get("subject")
    mirror.candidate_name = " ".join(
        part for part in [contact.get("firstName"), contact.get("lastName")] if part
    ) or None
    mirror.candidate_email = ticket.get("email") or contact.get("email")
    mirror.candidate_phone = ticket.get("phone") or contact.get("mobile")
    # Keep what the AI classified when Zoho has no value of its own, rather than
    # blanking the mirror the dashboard reads on every sync.
    mirror.campaign_name = cf.get("cf_campaign_name") or mirror.campaign_name
    mirror.issue_category = cf.get("cf_enquiry_type") or mirror.issue_category
    mirror.sentiment = ticket.get("sentiment")
    mirror.zoho_status = ticket.get("status")
    mirror.zoho_assignee_id = ticket.get("assigneeId")
    mirror.zoho_department_id = ticket.get("departmentId")
    mirror.zoho_web_url = ticket.get("webUrl")
    mirror.last_synced_at = datetime.utcnow()
    mirror.updated_at = datetime.utcnow()

    session.add(mirror)
    return mirror



def sync_ticket_mirror_from_zoho(
    session: Session,
    client,
    department_id: str | None = None,
    limit: int = 50,
    max_pages: int = 40,
) -> dict:
    """Mirror every currently-Open Zoho ticket, and reconcile ones that
    aren't Open anymore.

    Two things this fixes over a single-page fetch:

    1. PAGES through every Open ticket rather than stopping at the first
       `limit` — with more Open tickets than that in the department, the
       overflow used to be silently missed forever (each later sync still
       returns only the newest page, never reaching them).
    2. A ticket already mirrored as "Open" that the sweep no longer sees is
       refetched directly rather than left pinned at "Open" — otherwise a
       ticket closed weeks ago keeps being treated as pending indefinitely.

    `max_pages` bounds the loop (a defensive cap, not expected to bite in
    normal operation) so a client/pagination bug can't spin forever.
    """
    seen_ids: set[str] = set()
    from_index = 0
    for _ in range(max_pages):
        tickets = client.list_tickets(
            department_id=department_id,
            status="Open",
            limit=limit,
            from_index=from_index,
        )
        page = tickets.get("data") or []
        for ticket in page:
            upsert_ticket_mirror(session, ticket)
            seen_ids.add(str(ticket["id"]))
        if len(page) < limit:
            break
        from_index += limit
    session.commit()

    # Reconcile: a mirror we still have as "Open" that didn't come back in the
    # sweep above has moved on in Zoho (closed, reassigned, etc.) — refetch it
    # directly so it stops being treated as pending.
    stale_open = session.exec(
        select(HelpdeskTicketMirror)
        .where(HelpdeskTicketMirror.zoho_status == "Open")
        .where(HelpdeskTicketMirror.zoho_ticket_id.not_in(seen_ids or [""]))
    ).all()
    reconciled = 0
    for mirror in stale_open:
        try:
            ticket = client.get_ticket(mirror.zoho_ticket_id)
        except Exception:
            continue
        upsert_ticket_mirror(session, ticket)
        reconciled += 1
    if reconciled:
        session.commit()

    return {"synced": len(seen_ids), "reconciled": reconciled}

