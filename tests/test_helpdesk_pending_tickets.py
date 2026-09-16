from datetime import datetime, timedelta

from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_ai_service import find_pending_tickets


def make_mirror(ticket_id, status="Open", last_synced_at=None, zoho_modified_at=None):
    return HelpdeskTicketMirror(
        zoho_ticket_id=ticket_id,
        channel="Email",
        zoho_status=status,
        last_synced_at=last_synced_at or datetime.utcnow(),
        zoho_modified_at=zoho_modified_at,
    )


def test_never_processed_open_ticket_is_pending(session):
    session.add(make_mirror("t1"))
    session.commit()

    pending = find_pending_tickets(session)

    assert [m.zoho_ticket_id for m in pending] == ["t1"]


def test_closed_ticket_is_never_pending(session):
    session.add(make_mirror("t1", status="Closed"))
    session.commit()

    pending = find_pending_tickets(session)

    assert pending == []


def test_already_decided_ticket_with_no_new_sync_is_not_pending(session):
    now = datetime.utcnow()
    session.add(make_mirror("t1", last_synced_at=now - timedelta(hours=1)))
    session.add(HelpdeskAIAction(
        zoho_ticket_id="t1", action_type="draft_reply", rule="answerable_draft_first",
        created_at=now,  # decided AFTER the sync that produced this mirror state
    ))
    session.commit()

    pending = find_pending_tickets(session)

    assert pending == []


def test_ticket_modified_in_zoho_after_last_decision_is_pending_again(session):
    now = datetime.utcnow()
    session.add(HelpdeskAIAction(
        zoho_ticket_id="t1", action_type="route_to_human", rule="low_confidence",
        created_at=now - timedelta(hours=1),
    ))
    # The candidate replied in Zoho after we last decided -> re-process.
    session.add(make_mirror("t1", zoho_modified_at=now))
    session.commit()

    pending = find_pending_tickets(session)

    assert [m.zoho_ticket_id for m in pending] == ["t1"]


def test_unchanged_ticket_is_not_reprocessed_on_every_sync(session):
    """A plain re-sync must NOT make a decided ticket pending again.

    last_synced_at is our own clock and moves on every sync; treating it as a
    change signal re-classified, re-drafted and re-tagged every open ticket on
    every cron tick — hundreds of duplicate AI drafts a day on one ticket.
    """
    now = datetime.utcnow()
    session.add(HelpdeskAIAction(
        zoho_ticket_id="t1", action_type="draft_reply", rule="answerable_draft_first",
        created_at=now - timedelta(hours=1),
    ))
    # Synced just now, but Zoho says the ticket itself hasn't changed since
    # before our decision.
    session.add(make_mirror(
        "t1", last_synced_at=now, zoho_modified_at=now - timedelta(hours=2),
    ))
    session.commit()

    assert find_pending_tickets(session) == []


def test_limit_caps_the_number_of_pending_tickets_returned(session):
    for i in range(5):
        session.add(make_mirror(f"t{i}"))
    session.commit()

    pending = find_pending_tickets(session, limit=2)

    assert len(pending) == 2
