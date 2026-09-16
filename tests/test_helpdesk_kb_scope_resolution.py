"""_resolve_kb_scopes: turning what the classifier read off a ticket into
KB scope tags. Unlike voice (a caller states the campaign live), the
classifier extracts tool_name/campaign_name from the ticket's own text — so
a hallucinated or unmatched name must degrade to "no scope" (general-only),
never accidentally widen the search to some other tool's content.
"""

from app.models.campaign import Campaign
from app.services.helpdesk_ai_service import _resolve_kb_scopes


class _Classification:
    def __init__(self, tool_name=None, campaign_name=None):
        self.tool_name = tool_name
        self.campaign_name = campaign_name


def test_no_tool_or_campaign_resolves_to_no_scopes(session):
    tool_scope, campaign_scope = _resolve_kb_scopes(session, _Classification())
    assert tool_scope is None
    assert campaign_scope is None


def test_known_tool_name_resolves_to_its_scope(session):
    tool_scope, _ = _resolve_kb_scopes(session, _Classification(tool_name="Test Haven"))
    assert tool_scope == "test-haven"


def test_unknown_tool_name_is_ignored_not_passed_through(session):
    """A hallucinated/garbled tool name from the classifier must never reach
    the KB query as a scope tag — that could accidentally match a real scope
    that happens to share the slug, or just silently return nothing."""
    tool_scope, _ = _resolve_kb_scopes(session, _Classification(tool_name="SomeMadeUpTool"))
    assert tool_scope is None


def test_campaign_name_resolves_via_the_same_fuzzy_resolver_as_voice(session):
    session.add(Campaign(name="ExxonMobil Grad", kb_scope="exxonmobil", inbound_active=True))
    session.commit()

    _, campaign_scope = _resolve_kb_scopes(session, _Classification(campaign_name="ExxonMobil"))
    assert campaign_scope == "exxonmobil"


def test_unmatched_campaign_name_resolves_to_no_scope(session):
    _, campaign_scope = _resolve_kb_scopes(session, _Classification(campaign_name="Nonexistent Client"))
    assert campaign_scope is None


def test_inactive_campaign_does_not_resolve_a_scope(session):
    session.add(Campaign(name="Old Campaign", kb_scope="old", inbound_active=False))
    session.commit()

    _, campaign_scope = _resolve_kb_scopes(session, _Classification(campaign_name="Old Campaign"))
    assert campaign_scope is None


def test_tool_and_campaign_both_resolve_together(session):
    session.add(Campaign(
        name="Dangote PRP", kb_scope="dangote", tool_name="FOT", inbound_active=True,
    ))
    session.commit()

    tool_scope, campaign_scope = _resolve_kb_scopes(
        session, _Classification(tool_name="FOT", campaign_name="Dangote PRP"),
    )
    assert tool_scope == "fot"
    assert campaign_scope == "dangote"
