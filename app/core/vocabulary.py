"""Shared vocabulary/validation constants for recruiting assessment tools.

Defines the allow-list of assessment/exam tool names (e.g. "FOT", used
for Graduate Aptitude Test campaigns per docs/campaign-survey-outbound-csat-plan.md,
and "Scholastica") accepted for the `tool_name` field on campaigns,
candidates, and call records, plus a helper to validate against it.
"""

ALLOWED_TOOLS = {"FOT", "Scholastica"}

def is_tool_allowed(tool_name: str) -> bool:
    """Return True if `tool_name` is one of the recognized assessment tools in `ALLOWED_TOOLS`."""
    return tool_name in ALLOWED_TOOLS