"""Campaign outbound call-context: creation, editing, validation, and that it
reaches the ElevenLabs agent as dynamic variables (the outreach script's
[brackets])."""

from datetime import datetime

from app.integrations.elevenlabs_outbound_client import (
    build_outbound_dynamic_variables,
)
from app.models.campaign import Campaign
from app.models.outbound_call_attempt import OutboundCallAttempt


def test_create_campaign_with_outbound_context(client):
    payload = {
        "name": "Sept Graduate Assessment",
        "call_reason": "reminder",
        "organization_name": "Dragnet",
        "assessment_at": "2026-09-02T10:00:00",
        "assessment_location": "Lagos test centre",
        "practice_test_url": "https://practice.example.com",
        "contact_info": "0800-000-0000",
    }
    resp = client.post("/campaigns", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["call_reason"] == "reminder"
    assert data["organization_name"] == "Dragnet"
    assert data["assessment_location"] == "Lagos test centre"


def test_create_campaign_rejects_unknown_call_reason(client):
    resp = client.post(
        "/campaigns", json={"name": "Bad Reason", "call_reason": "not_a_reason"}
    )
    assert resp.status_code == 422


def test_update_outbound_settings_partial(client):
    created = client.post("/campaigns", json={"name": "Edit Me"}).json()
    cid = created["id"]

    resp = client.patch(
        f"/campaigns/{cid}/outbound-settings",
        json={"call_reason": "no_show_followup", "contact_info": "help@dragnet.test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_reason"] == "no_show_followup"
    assert data["contact_info"] == "help@dragnet.test"
    # Untouched field stays as it was.
    assert data["name"] == "Edit Me"


def test_update_outbound_settings_unknown_campaign_404(client):
    resp = client.patch(
        "/campaigns/nope/outbound-settings", json={"call_reason": "reminder"}
    )
    assert resp.status_code == 404


def test_dynamic_variables_include_campaign_context():
    campaign = Campaign(
        name="Grad Push",
        call_reason="reminder",
        organization_name="Dragnet",
        assessment_at=datetime(2026, 9, 2, 10, 0, 0),
        assessment_location="Lagos test centre",
        practice_test_url="https://practice.example.com",
        contact_info="0800-000-0000",
    )
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id,
        candidate_id="cand1",
        candidate_name="Ada",
        phone="+2348010000000",
        attempt_number=1,
    )

    variables = build_outbound_dynamic_variables(attempt, campaign)

    assert variables["candidate_name"] == "Ada"
    assert variables["call_reason"] == "reminder"
    assert variables["organization_name"] == "Dragnet"
    assert variables["assessment_location"] == "Lagos test centre"
    assert variables["practice_test_url"] == "https://practice.example.com"
    # Assessment time is formatted into a speakable string.
    assert "September" in variables["assessment_at"]
    assert "10:00" in variables["assessment_at"]


def test_dynamic_variables_coerce_missing_context_to_empty_strings():
    # A campaign with no outbound context yet -> agent still gets concrete
    # (empty) values, never None.
    campaign = Campaign(name="Bare")
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id="c", phone="+2348010000000",
    )
    variables = build_outbound_dynamic_variables(attempt, campaign)
    assert variables["call_reason"] == ""
    assert variables["assessment_at"] == ""
    # Organization falls back to the campaign name.
    assert variables["organization_name"] == "Bare"


def test_time_of_day_greeting_boundaries():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.integrations.elevenlabs_outbound_client import _time_of_day_greeting

    lagos = ZoneInfo("Africa/Lagos")
    assert _time_of_day_greeting(datetime(2026, 9, 2, 6, 0, tzinfo=lagos)) == "Good morning"
    assert _time_of_day_greeting(datetime(2026, 9, 2, 11, 59, tzinfo=lagos)) == "Good morning"
    assert _time_of_day_greeting(datetime(2026, 9, 2, 12, 0, tzinfo=lagos)) == "Good afternoon"
    assert _time_of_day_greeting(datetime(2026, 9, 2, 16, 59, tzinfo=lagos)) == "Good afternoon"
    assert _time_of_day_greeting(datetime(2026, 9, 2, 17, 0, tzinfo=lagos)) == "Good evening"
    assert _time_of_day_greeting(datetime(2026, 9, 2, 23, 0, tzinfo=lagos)) == "Good evening"


def test_dynamic_variables_include_a_time_of_day_greeting():
    campaign = Campaign(name="Grad Push")
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id="c", phone="+2348010000000",
    )
    variables = build_outbound_dynamic_variables(attempt, campaign)
    assert variables["time_of_day_greeting"] in (
        "Good morning", "Good afternoon", "Good evening",
    )


def test_dynamic_variables_without_campaign_still_define_every_key():
    # Even with no campaign, the campaign keys must be DEFINED (empty), never
    # absent — the agent's prompt referencing an undefined variable is worse
    # than referencing a blank one it can handle.
    attempt = OutboundCallAttempt(
        campaign_id="c1", candidate_id="c", phone="+2348010000000",
    )
    variables = build_outbound_dynamic_variables(attempt)

    assert variables["outbound_attempt_id"] == attempt.id
    for key in (
        "call_reason", "organization_name", "assessment_at",
        "assessment_location", "practice_test_url", "contact_info",
    ):
        assert variables[key] == ""
    assert all(isinstance(v, str) for v in variables.values())
