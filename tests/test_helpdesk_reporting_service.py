from datetime import datetime, timedelta

from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.services.helpdesk_reporting_service import get_helpdesk_summary


def make_action(ticket_id, action_type, rule, category=None, grounding=None, sensitive=False, executed=False, created_at=None):
    return HelpdeskAIAction(
        zoho_ticket_id=ticket_id,
        action_type=action_type,
        rule=rule,
        issue_category=category,
        grounding_status=grounding,
        sensitivity_detected=sensitive,
        executed=executed,
        created_at=created_at or datetime.utcnow(),
    )


def test_summary_tallies_action_and_rule_counts(session):
    session.add(make_action("t1", "draft_reply", "answerable_draft_first", category="technical_issue"))
    session.add(make_action("t2", "route_to_human", "sensitive_never_automated", category="complaint", sensitive=True))
    session.add(make_action("t3", "tag_only", "spam_no_reply", category="spam_or_irrelevant"))
    session.commit()

    summary = get_helpdesk_summary(session)

    assert summary["total_tickets"] == 3
    assert summary["action_counts"] == {"draft_reply": 1, "route_to_human": 1, "tag_only": 1}
    assert summary["sensitive_count"] == 1


def test_summary_collapses_to_latest_action_per_ticket(session):
    # Same ticket processed twice (re-processed after a new candidate reply)
    older = make_action("t1", "route_to_human", "low_confidence", created_at=datetime.utcnow() - timedelta(hours=1))
    newer = make_action("t1", "draft_reply", "answerable_draft_first", category="technical_issue")
    session.add(older)
    session.add(newer)
    session.commit()

    summary = get_helpdesk_summary(session)

    assert summary["total_tickets"] == 1
    assert summary["action_counts"] == {"draft_reply": 1}


def test_kb_gap_and_draft_execution_counts(session):
    session.add(make_action("t1", "route_to_human", "kb_grounding_missing", grounding="missing"))
    session.add(make_action("t2", "draft_reply", "answerable_draft_first", grounding="grounded", executed=False))
    session.add(make_action("t3", "draft_reply", "answerable_draft_first", grounding="grounded", executed=True))
    session.commit()

    summary = get_helpdesk_summary(session)

    assert summary["kb_gap_count"] == 1
    assert summary["drafts_awaiting_execution"] == 1
    assert summary["drafts_placed_on_zoho"] == 1


def test_automation_rate_excludes_route_to_human(session):
    session.add(make_action("t1", "draft_reply", "answerable_draft_first"))
    session.add(make_action("t2", "tag_only", "spam_no_reply"))
    session.add(make_action("t3", "route_to_human", "sensitive_never_automated"))
    session.add(make_action("t4", "route_to_human", "sensitive_never_automated"))
    session.commit()

    summary = get_helpdesk_summary(session)

    # 2 automated (draft+tag) out of 4 total
    assert summary["automation_rate"] == 0.5


def test_category_action_mix_per_category(session):
    session.add(make_action("t1", "draft_reply", "answerable_draft_first", category="technical_issue"))
    session.add(make_action("t2", "draft_reply", "answerable_draft_first", category="technical_issue"))
    session.add(make_action("t3", "route_to_human", "kb_grounding_missing", category="technical_issue", grounding="missing"))
    session.commit()

    summary = get_helpdesk_summary(session)

    assert summary["category_action_mix"]["technical_issue"] == {
        "draft_reply": 2,
        "route_to_human": 1,
    }


def test_empty_db_summary_does_not_crash(session):
    summary = get_helpdesk_summary(session)

    assert summary["total_tickets"] == 0
    assert summary["automation_rate"] == 0.0
