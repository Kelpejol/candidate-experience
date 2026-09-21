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
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror


def list_recent_ai_actions(
    session: Session,
    action_type: str | None = None,
    only_drafts: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Recent AI decisions for the review UI, newest first.

    Each row is the AI action enriched with the ticket's subject / candidate
    email / channel from the mirror, so a reviewer can read what the AI
    decided (and the draft it wrote) without cross-referencing Zoho. Optional
    filters: a specific action_type, or only rows that produced a draft.
    """
    statement = select(HelpdeskAIAction).order_by(HelpdeskAIAction.created_at.desc())
    if action_type:
        statement = statement.where(HelpdeskAIAction.action_type == action_type)
    if only_drafts:
        statement = statement.where(HelpdeskAIAction.draft_text.is_not(None))
    actions = session.exec(statement.limit(limit)).all()

    ticket_ids = {a.zoho_ticket_id for a in actions}
    mirrors: dict[str, HelpdeskTicketMirror] = {}
    if ticket_ids:
        for mirror in session.exec(
            select(HelpdeskTicketMirror).where(
                HelpdeskTicketMirror.zoho_ticket_id.in_(ticket_ids)
            )
        ).all():
            mirrors[mirror.zoho_ticket_id] = mirror

    rows: list[dict] = []
    for action in actions:
        mirror = mirrors.get(action.zoho_ticket_id)
        rows.append({
            "id": action.id,
            "zoho_ticket_id": action.zoho_ticket_id,
            "subject": mirror.subject if mirror else None,
            "candidate_email": mirror.candidate_email if mirror else None,
            "channel": mirror.channel if mirror else None,
            "action_type": action.action_type,
            "rule": action.rule,
            "issue_category": action.issue_category,
            "confidence_label": action.confidence_label,
            "sensitivity_detected": action.sensitivity_detected,
            "grounding_status": action.grounding_status,
            "draft_text": action.draft_text,
            "reason": action.reason,
            "executed": action.executed,
            "created_at": action.created_at,
        })
    return rows


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
    automated_actions = {"draft_reply", "auto_reply", "ask_clarification", "request_attachment", "tag_only"}
    automation_rate = round(sum(action_counts.get(a, 0) for a in automated_actions) / total, 3) if total else 0.0

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
