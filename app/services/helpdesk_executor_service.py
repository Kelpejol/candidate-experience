"""Writes AI decisions into Zoho: tags on every ticket, plus an optional
assignment for route_to_human when a routing map is configured.

Zoho account has one department and no sub-teams (checked July 2026) — six
named agents plus a shared tech inbox. Guessing "route complaints to
Elizabeth" without the team's say-so would be a real, visible mistake (a
misrouted ticket someone has to notice and fix), so ROUTING_ASSIGNEE_BY_CATEGORY
starts empty: routed tickets are tagged clearly and left in the shared queue
for whoever is on duty, not silently reassigned. Fill the map in once the
team decides who owns what; nothing else needs to change.

Gated by settings.helpdesk_tag_execute (default False) — same reasoning as
helpdesk_draft_execute: this is a live-ticket write, so it waits for the
officer heads-up regardless of how low-risk it looks in code.
"""

from app.core.config import get_settings
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror

# issue_category -> Zoho agent id. Empty on purpose (see module docstring).
# Populate from `python scripts/check_zoho_agents.py` once the team decides,
# e.g. {"complaint": "1010551000052709040"}.
ROUTING_ASSIGNEE_BY_CATEGORY: dict[str, str] = {}

SENSITIVE_PRIORITY = "High"


def build_tags_for_action(action: HelpdeskAIAction) -> list[str]:
    """Tags describing what the AI decided — visible to officers in Zoho,
    lets someone watching the queue triage without opening every ticket."""
    tags = [f"ai_{action.action_type}"]
    if action.issue_category:
        tags.append(f"category_{action.issue_category}")
    if action.sensitivity_detected:
        tags.append("sensitive_issue")
    if action.grounding_status == "missing":
        tags.append("kb_gap")
    return tags


def execute_ticket_action(
    zoho_client, mirror: HelpdeskTicketMirror, action: HelpdeskAIAction
) -> bool:
    """Write tags (and, for route_to_human, a mapped assignment) to Zoho.

    Returns True if a write was made, False if the tag-execute flag is off
    (nothing touched) — the caller ORs this into HelpdeskAIAction.executed.
    """
    if not get_settings().helpdesk_tag_execute:
        return False

    # Tags go through the dedicated /associateTag endpoint — a ticket PATCH
    # with a `tags` field is rejected 422 (verified against live Zoho).
    zoho_client.associate_tags(mirror.zoho_ticket_id, build_tags_for_action(action))

    # Priority/assignee, on the other hand, ARE real ticket fields and only
    # apply to a route_to_human. Patch them only when there's something to set.
    fields: dict = {}
    if action.action_type == "route_to_human":
        if action.sensitivity_detected:
            fields["priority"] = SENSITIVE_PRIORITY
        assignee_id = ROUTING_ASSIGNEE_BY_CATEGORY.get(action.issue_category or "")
        if assignee_id:
            fields["assigneeId"] = assignee_id

    if fields:
        zoho_client.update_ticket(mirror.zoho_ticket_id, fields)

    return True
