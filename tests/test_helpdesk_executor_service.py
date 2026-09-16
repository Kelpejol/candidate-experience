from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_executor_service import (
    build_tags_for_action,
    execute_ticket_action,
)


class FakeZohoClient:
    def __init__(self):
        self.update_calls = []
        self.tag_calls = []

    def update_ticket(self, ticket_id, fields):
        self.update_calls.append((ticket_id, fields))

    def associate_tags(self, ticket_id, tags):
        self.tag_calls.append((ticket_id, tags))


def make_action(action_type="route_to_human", category="technical_issue", sensitive=False, grounding=None):
    return HelpdeskAIAction(
        zoho_ticket_id="12345", action_type=action_type, rule="test",
        issue_category=category, sensitivity_detected=sensitive, grounding_status=grounding,
    )


def make_mirror():
    return HelpdeskTicketMirror(zoho_ticket_id="12345", channel="Email")


def test_build_tags_includes_action_category_and_flags():
    action = make_action(category="complaint", sensitive=True, grounding="missing")
    tags = build_tags_for_action(action)

    assert "ai_route_to_human" in tags
    assert "category_complaint" in tags
    assert "sensitive_issue" in tags
    assert "kb_gap" in tags


def test_disabled_writes_nothing(monkeypatch):
    from app.core.config import get_settings

    # Explicitly off (don't rely on the ambient .env, which now enables tags).
    monkeypatch.setenv("HELPDESK_TAG_EXECUTE", "false")
    get_settings.cache_clear()

    zoho = FakeZohoClient()
    result = execute_ticket_action(zoho, make_mirror(), make_action())

    assert result is False
    assert zoho.update_calls == []
    assert zoho.tag_calls == []

    get_settings.cache_clear()


def test_enabled_flag_writes_tags(monkeypatch):
    from app.core.config import Settings, get_settings

    monkeypatch.setenv("HELPDESK_TAG_EXECUTE", "true")
    get_settings.cache_clear()

    zoho = FakeZohoClient()
    result = execute_ticket_action(zoho, make_mirror(), make_action())

    assert result is True
    # Tags go through associate_tags, not a ticket PATCH.
    assert len(zoho.tag_calls) == 1
    ticket_id, tags = zoho.tag_calls[0]
    assert ticket_id == "12345"
    assert "ai_route_to_human" in tags

    get_settings.cache_clear()


def test_sensitive_route_gets_high_priority(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HELPDESK_TAG_EXECUTE", "true")
    get_settings.cache_clear()

    zoho = FakeZohoClient()
    execute_ticket_action(zoho, make_mirror(), make_action(sensitive=True))

    _, fields = zoho.update_calls[0]
    assert fields["priority"] == "High"

    get_settings.cache_clear()


def test_no_assignee_without_a_routing_map_entry(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HELPDESK_TAG_EXECUTE", "true")
    get_settings.cache_clear()

    zoho = FakeZohoClient()
    # technical_issue is not sensitive and has no routing map entry, so there
    # is nothing to PATCH — only the tag write happens.
    execute_ticket_action(zoho, make_mirror(), make_action(category="technical_issue"))

    assert zoho.update_calls == []
    assert len(zoho.tag_calls) == 1

    get_settings.cache_clear()


def test_tag_only_action_never_gets_priority_or_assignee(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HELPDESK_TAG_EXECUTE", "true")
    get_settings.cache_clear()

    zoho = FakeZohoClient()
    # A tag_only action (even if flagged sensitive) never patches priority/
    # assignee — those are route_to_human-only — so it makes no PATCH at all.
    execute_ticket_action(zoho, make_mirror(), make_action(action_type="tag_only", sensitive=True))

    assert zoho.update_calls == []
    assert len(zoho.tag_calls) == 1

    get_settings.cache_clear()
