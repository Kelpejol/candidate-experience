from datetime import datetime, timedelta

import pytest

from app.models.campaign import Campaign
from app.services.campaign_scope_service import (
    campaign_inbound_available,
    campaign_scope,
    campaign_tool_scope,
    resolve_campaign,
    tool_name_to_scope,
)

NOW = datetime(2026, 8, 24, 12, 0, 0)


def mk(**kw):
    return Campaign(name=kw.pop("name", "Test"), **kw)


# --- availability ---------------------------------------------------------

def test_available_when_active_and_no_window():
    assert campaign_inbound_available(mk(inbound_active=True), NOW) is True


def test_unavailable_when_switched_off():
    assert campaign_inbound_available(mk(inbound_active=False), NOW) is False


def test_unavailable_before_window():
    assert campaign_inbound_available(mk(active_from=NOW + timedelta(days=1)), NOW) is False


def test_unavailable_after_window():
    assert campaign_inbound_available(mk(active_until=NOW - timedelta(days=1)), NOW) is False


def test_available_within_window():
    c = mk(active_from=NOW - timedelta(days=1), active_until=NOW + timedelta(days=1))
    assert campaign_inbound_available(c, NOW) is True


# --- scope ----------------------------------------------------------------

def test_scope_uses_kb_scope_then_falls_back_to_id():
    assert campaign_scope(mk(kb_scope="dangote")) == "dangote"
    c = mk()
    assert campaign_scope(c) == c.id


# --- resolve (needs the DB session) ---------------------------------------

def persist(session, **kw):
    c = mk(**kw)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def test_resolve_found_active(session):
    persist(session, name="Dangote PRP Graduate Trainee", kb_scope="dangote", inbound_active=True)
    r = resolve_campaign(session, "dangote", NOW)
    assert r["status"] == "found"
    assert r["scope"] == "dangote"


def test_resolve_inactive_campaign(session):
    persist(session, name="NNPC Feedback", inbound_active=False)
    r = resolve_campaign(session, "nnpc", NOW)
    assert r["status"] == "inactive"
    assert r["scope"] is None


def test_resolve_out_of_window_is_inactive(session):
    persist(session, name="Shell Grad", inbound_active=True,
            active_until=NOW - timedelta(days=1))
    assert resolve_campaign(session, "shell", NOW)["status"] == "inactive"


def test_resolve_not_found(session):
    persist(session, name="Dangote", inbound_active=True)
    assert resolve_campaign(session, "totallyunknown", NOW)["status"] == "not_found"


def test_resolve_ambiguous(session):
    persist(session, name="Dangote Graduate", inbound_active=True)
    persist(session, name="Dangote Experienced", inbound_active=True)
    assert resolve_campaign(session, "dangote", NOW)["status"] == "ambiguous"


def test_resolve_case_insensitive_substring(session):
    persist(session, name="Dangote PRP Graduate Trainee — Feedback",
            kb_scope="dangote", inbound_active=True)
    assert resolve_campaign(session, "DANGOTE", NOW)["status"] == "found"


def test_resolve_empty_name(session):
    persist(session, name="Dangote", inbound_active=True)
    assert resolve_campaign(session, "   ", NOW)["status"] == "not_found"


# --- tool scope (third KB tier: general + tool + campaign) -----------------

def test_tool_name_to_scope_slugs_the_name():
    assert tool_name_to_scope("Test Haven") == "test-haven"
    assert tool_name_to_scope("FOT") == "fot"
    assert tool_name_to_scope("Scholastica") == "scholastica"


def test_campaign_tool_scope_none_when_no_tool_set():
    assert campaign_tool_scope(mk(tool_name=None)) is None


def test_campaign_tool_scope_resolved_from_tool_name():
    assert campaign_tool_scope(mk(tool_name="Test Haven")) == "test-haven"
    assert campaign_tool_scope(mk(tool_name="FOT")) == "fot"


def test_resolve_campaign_includes_tool_scope_when_found(session):
    persist(session, name="ExxonMobil Grad", kb_scope="exxonmobil",
            tool_name="Test Haven", inbound_active=True)
    result = resolve_campaign(session, "exxonmobil", NOW)
    assert result["status"] == "found"
    assert result["scope"] == "exxonmobil"
    assert result["tool_scope"] == "test-haven"


def test_resolve_campaign_tool_scope_none_when_campaign_has_no_tool(session):
    persist(session, name="Generic Campaign", inbound_active=True)
    result = resolve_campaign(session, "generic", NOW)
    assert result["status"] == "found"
    assert result["tool_scope"] is None


def test_resolve_campaign_tool_scope_none_for_every_non_found_status(session):
    persist(session, name="Dangote", inbound_active=False)
    assert resolve_campaign(session, "dangote", NOW)["tool_scope"] is None
    assert resolve_campaign(session, "totallyunknown", NOW)["tool_scope"] is None


# --- garbled speech: "did you mean...?" instead of dead-ending -------------

# The live campaign names, so the fuzz below is scored against what callers
# actually hear and mispronounce.
LIVE_NAMES = [
    "Dangote PRP Graduate Trainee — Feedback",
    "Chevron Operator Skills — CSAT",
    "Lafarge Africa Assessment — Feedback",
    "MTNN Graduate Scheme — CSAT",
    "ND Western SITP — Feedback",
    "Tatum Bank Sales Trainee — Feedback",
    "Matrix Energy Group — CSAT",
]


def seed_live(session):
    for name in LIVE_NAMES:
        persist(session, name=name, inbound_active=True)


def test_real_asr_failure_anguti_suggests_dangote(session):
    """The failure seen on a real call: ASR heard "Anguti" for "Dangote" and
    the agent escalated instead of offering the obvious near-match."""
    seed_live(session)
    result = resolve_campaign(session, "Anguti", NOW)
    assert result["status"] == "did_you_mean"
    assert result["options"] == ["Dangote PRP Graduate Trainee — Feedback"]


@pytest.mark.parametrize("spoken,expected_sponsor", [
    ("Anguti", "Dangote"),          # ASR dropped the leading D
    ("Dangote PLP", "Dangote"),     # heard PLP for PRP
    ("Dan Gote", "Dangote"),        # split into two words
    ("Dangoat", "Dangote"),
    ("Shevron", "Chevron"),
    ("Chevron FOT", "Chevron"),     # real org plus the platform name
    ("La Farge", "Lafarge"),
    ("Lafarj", "Lafarge"),
    ("MTN", "MTNN"),
    ("And Western", "ND Western"),
    ("Tatem", "Tatum"),
    ("Matrics", "Matrix"),
])
def test_garbled_names_suggest_the_right_campaign(session, spoken, expected_sponsor):
    seed_live(session)
    result = resolve_campaign(session, spoken, NOW)
    assert result["status"] in {"did_you_mean", "found"}, f"{spoken} dead-ended"
    offered = result["options"] or [result["name"]]
    assert any(expected_sponsor.lower() in o.lower() for o in offered), (
        f"{spoken!r} offered {offered}, expected {expected_sponsor}"
    )


@pytest.mark.parametrize("spoken", [
    "Shell", "Coca Cola", "Total Energies", "Access Bank", "Google", "Tottenham",
])
def test_a_different_company_is_never_suggested_a_campaign(session, spoken):
    """A caller naming another company must not be offered an unrelated
    campaign that happens to share a generic word ("Total Energies" must not
    become "Matrix Energy Group")."""
    seed_live(session)
    result = resolve_campaign(session, spoken, NOW)
    assert result["status"] == "not_found", f"{spoken} wrongly matched {result}"


def test_a_suggestion_never_resolves_a_scope_on_its_own(session):
    """Fuzzy matches are read back for confirmation, never used to pick a KB."""
    seed_live(session)
    result = resolve_campaign(session, "Anguti", NOW)
    assert result["scope"] is None
    assert result["tool_scope"] is None


def test_not_found_offers_the_real_campaigns_instead_of_dead_ending(session):
    seed_live(session)
    result = resolve_campaign(session, "Google", NOW)
    assert result["status"] == "not_found"
    assert result["available_count"] == len(LIVE_NAMES)
    assert "Chevron Operator Skills — CSAT" in result["available"]


def test_ambiguous_now_names_the_options(session):
    persist(session, name="Dangote Graduate", inbound_active=True)
    persist(session, name="Dangote Experienced", inbound_active=True)
    result = resolve_campaign(session, "dangote", NOW)
    assert result["status"] == "ambiguous"
    assert sorted(result["options"]) == ["Dangote Experienced", "Dangote Graduate"]


def test_naming_the_platform_is_redirected_not_rejected(session):
    """Candidates aren't told FOT from Test Haven, but they read it on screen
    and say it. The agent should redirect, not dead-end."""
    seed_live(session)
    result = resolve_campaign(session, "FOT", NOW)
    assert result["status"] == "tool_not_campaign"
    assert result["name"] == "FOT"
    assert result["available_count"] == len(LIVE_NAMES)


def test_suggestions_are_capped_for_a_phone_call(session):
    for i in range(6):
        persist(session, name=f"Dangote Programme {i}", inbound_active=True)
    result = resolve_campaign(session, "Dangoat", NOW)
    assert len(result["options"]) <= 3


def test_exact_match_still_wins_outright(session):
    seed_live(session)
    result = resolve_campaign(session, "Chevron", NOW)
    assert result["status"] == "found"
    assert result["options"] == []
