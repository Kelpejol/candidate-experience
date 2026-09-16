"""Shared vocabulary/validation constants for recruiting assessment tools.

Defines the allow-list of assessment/exam tool names ("FOT" and "Test Haven"
— the two test-delivery platforms — and "Scholastica", a separate product)
accepted for the `tool_name` field on campaigns, candidates, and call
records, plus a helper to validate against it. A candidate never states this
themselves on a call — it's resolved server-side from the campaign they
mention (see campaign_scope_service.campaign_tool_scope).
"""

ALLOWED_TOOLS = {"FOT", "Test Haven", "Scholastica"}

def is_tool_allowed(tool_name: str) -> bool:
    """Return True if `tool_name` is one of the recognized assessment tools in `ALLOWED_TOOLS`."""
    return tool_name in ALLOWED_TOOLS


# Why the outbound agent is calling. Selects which version of the core message
# (Step 4 of the outreach script) the agent uses.
OUTBOUND_CALL_REASONS = {
    "schedule_confirmation",  # "Have you received the email invite?"
    "reminder",               # "Your test is scheduled for …"
    "no_show_followup",       # "You didn't take the assessment — reschedule?"
}


def is_call_reason_allowed(call_reason: str) -> bool:
    """Return True if `call_reason` is one of OUTBOUND_CALL_REASONS."""
    return call_reason in OUTBOUND_CALL_REASONS