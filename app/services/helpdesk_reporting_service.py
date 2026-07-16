"""Reporting over the Helpdesk AI audit trail (HelpdeskAIAction).

Answers "is the automation actually helping" (PSA Phase 4 / plan step 9):
action mix, which rule decided each outcome, per-category automation rate,
and how often the KB simply doesn't cover the question (a KB content gap,
not a bug).

Dataset is small (hundreds of rows at this stage) so this reads everything
into memory and tallies in Python rather than hand-rolling SQL GROUP BYs —
simplest correct thing at this scale; revisit with real aggregate queries
if/when the table grows into the tens of thousands of rows.
"""

from collections import Counter

from sqlmodel import Session, select

from app.models.helpdesk_ai_action import HelpdeskAIAction


def _latest_action_per_ticket(session: Session) -> list[HelpdeskAIAction]:
    """Collapse the audit log to one (most recent) row per ticket.

    A ticket can be processed more than once (re-processed after a new
    candidate reply); reporting should reflect the *current* decision on
    each ticket, not double-count history.
    """
    all_actions = session.exec(
        select(HelpdeskAIAction).order_by(HelpdeskAIAction.created_at.asc())
    ).all()

    latest_by_ticket: dict[str, HelpdeskAIAction] = {}
    for action in all_actions:
        latest_by_ticket[action.zoho_ticket_id] = action  # last write wins (ascending order)
    return list(latest_by_ticket.values())


def get_helpdesk_summary(session: Session) -> dict:
    """One summary dict covering the metrics the plan doc's reporting step asks for."""
    actions = _latest_action_per_ticket(session)

    action_counts = Counter(a.action_type for a in actions)
    rule_counts = Counter(a.rule for a in actions)
    category_counts = Counter(a.issue_category for a in actions if a.issue_category)
    confidence_counts = Counter(a.confidence_label for a in actions if a.confidence_label)

    kb_gap_count = sum(1 for a in actions if a.grounding_status == "missing")
    sensitive_count = sum(1 for a in actions if a.sensitivity_detected)
    drafts_awaiting_execution = sum(
        1 for a in actions if a.action_type == "draft_reply" and not a.executed
    )
    drafts_placed_on_zoho = sum(
        1 for a in actions if a.action_type == "draft_reply" and a.executed
    )

    total = len(actions)
    automation_rate = round((action_counts.get("draft_reply", 0) + action_counts.get("tag_only", 0)) / total, 3) if total else 0.0

    # Per-category action mix — which categories the AI actually handles vs
    # routes away, the evidence base for later auto-send promotion (plan step 8).
    category_action_mix: dict[str, dict[str, int]] = {}
    for action in actions:
        if not action.issue_category:
            continue
        bucket = category_action_mix.setdefault(action.issue_category, {})
        bucket[action.action_type] = bucket.get(action.action_type, 0) + 1

    return {
        "total_tickets": total,
        "action_counts": dict(action_counts),
        "rule_counts": dict(rule_counts),
        "issue_category_counts": dict(category_counts),
        "confidence_counts": dict(confidence_counts),
        "kb_gap_count": kb_gap_count,
        "sensitive_count": sensitive_count,
        "drafts_awaiting_execution": drafts_awaiting_execution,
        "drafts_placed_on_zoho": drafts_placed_on_zoho,
        "automation_rate": automation_rate,
        "category_action_mix": category_action_mix,
    }
