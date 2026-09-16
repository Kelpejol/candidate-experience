from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_reporting_service import list_recent_ai_actions


def seed(session):
    session.add(HelpdeskTicketMirror(
        zoho_ticket_id="t1", channel="Email",
        candidate_email="a@example.com", subject="Cannot access test",
    ))
    session.add(HelpdeskAIAction(
        zoho_ticket_id="t1", action_type="draft_reply", rule="grounded",
        issue_category="technical_issue", draft_text="Dear A, try again.",
    ))
    session.add(HelpdeskAIAction(
        zoho_ticket_id="t2", action_type="route_to_human", rule="kb_grounding_missing",
    ))
    session.commit()


def test_list_enriches_with_ticket_context(session):
    seed(session)
    rows = list_recent_ai_actions(session)
    drafted = next(r for r in rows if r["action_type"] == "draft_reply")
    assert drafted["subject"] == "Cannot access test"
    assert drafted["candidate_email"] == "a@example.com"
    assert drafted["channel"] == "Email"
    assert drafted["draft_text"] == "Dear A, try again."


def test_filter_by_action_type(session):
    seed(session)
    rows = list_recent_ai_actions(session, action_type="route_to_human")
    assert len(rows) == 1
    assert rows[0]["action_type"] == "route_to_human"


def test_only_drafts_filter(session):
    seed(session)
    rows = list_recent_ai_actions(session, only_drafts=True)
    assert len(rows) == 1
    assert rows[0]["draft_text"] is not None


def test_missing_mirror_leaves_context_none(session):
    # An action whose ticket isn't mirrored still lists, just without context.
    session.add(HelpdeskAIAction(zoho_ticket_id="orphan", action_type="tag_only", rule="x"))
    session.commit()
    rows = list_recent_ai_actions(session)
    assert rows[0]["subject"] is None
    assert rows[0]["candidate_email"] is None
