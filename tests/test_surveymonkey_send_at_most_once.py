"""send_campaign_survey_message must be at-most-once: a real survey email
should never go out twice, even on a retry or a mid-send crash."""

import pytest

from app.core.config import get_settings
from app.models.campaign import Campaign
from app.services import surveymonkey_distribution_service as sm_dist
from app.services.surveymonkey_distribution_service import send_campaign_survey_message


def _campaign(session, **kw):
    campaign = Campaign(
        name="Send Test", surveymonkey_collector_id="collector_001", **kw
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


def _configure(monkeypatch, fake_client_cls):
    monkeypatch.setenv("SURVEYMONKEY_ACCESS_TOKEN", "token")
    get_settings.cache_clear()
    monkeypatch.setattr(sm_dist, "SurveyMonkeyClient", fake_client_cls)


class _FakeClient:
    sends: list = []

    def __init__(self, base_url, access_token):
        pass

    def send_collector_message(self, collector_id, message_id):
        _FakeClient.sends.append((collector_id, message_id))
        return {"status": "sent"}


def test_a_second_send_is_refused_once_the_campaign_is_marked_sent(
    session, monkeypatch
):
    _FakeClient.sends = []
    _configure(monkeypatch, _FakeClient)
    campaign = _campaign(session, status="survey_sent")  # already sent

    with pytest.raises(ValueError, match="already"):
        send_campaign_survey_message(
            campaign=campaign, message_id="m1",
            confirm_send="SEND_SURVEY_EMAILS", session=session,
        )
    assert _FakeClient.sends == []


def test_a_crash_mid_send_leaves_the_campaign_unretryable_by_default(
    session, monkeypatch
):
    """The realistic crash: SurveyMonkey has already sent, but the process dies
    before candidates are marked sent. The campaign must land somewhere a naive
    retry can't reach — not back at a state that looks send-ready."""

    class CrashesAfterSend(_FakeClient):
        def send_collector_message(self, collector_id, message_id):
            super().send_collector_message(collector_id, message_id)
            raise RuntimeError("worker killed right after the send")

    _FakeClient.sends = []
    _configure(monkeypatch, CrashesAfterSend)
    campaign = _campaign(session, status="uploaded")

    with pytest.raises(RuntimeError):
        send_campaign_survey_message(
            campaign=campaign, message_id="m1",
            confirm_send="SEND_SURVEY_EMAILS", session=session,
        )

    session.refresh(campaign)
    assert campaign.status == "survey_sending"

    # A second call — the shape of an automatic retry — is refused rather than
    # re-sending, because "survey_sending" is one of the in-flight statuses.
    with pytest.raises(ValueError, match="already"):
        send_campaign_survey_message(
            campaign=campaign, message_id="m1",
            confirm_send="SEND_SURVEY_EMAILS", session=session,
        )
    assert len(CrashesAfterSend.sends) == 1  # not sent twice


def test_a_genuine_first_send_still_succeeds(session, monkeypatch):
    _FakeClient.sends = []
    _configure(monkeypatch, _FakeClient)
    campaign = _campaign(session, status="uploaded")

    result = send_campaign_survey_message(
        campaign=campaign, message_id="m1",
        confirm_send="SEND_SURVEY_EMAILS", session=session,
    )

    assert result["sent"] is True
    assert _FakeClient.sends == [("collector_001", "m1")]
    session.refresh(campaign)
    assert campaign.status == "survey_sent"
