"""Regressions for defects found while auditing the outbound agent.

Each test pins a specific failure that was live in the code: a person opted out
in one campaign being called by the next, an answer silently overwritten, a
stuck call parking a campaign forever, and a timezone-aware assessment time
being spoken as the wrong hour.
"""

from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.jobs import campaign_orchestration_jobs as orch_jobs
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.models.outbound_survey_answer import OutboundSurveyAnswer
from app.schemas.campaign import CampaignCreate, CampaignOutboundUpdate
from app.services.campaign_service import (
    add_campaign_candidates,
    build_outbound_call_queue,
)
from app.schemas.campaign import CampaignCandidateCreate
from app.services.outbound_survey_service import record_opt_out, record_survey_answer


@pytest.fixture(autouse=True)
def _open_endpoints(monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _campaign(session, name):
    campaign = Campaign(name=name, status="outbound_ready")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


def _add_person(session, campaign, *, email, phone, name="Ada"):
    added = add_campaign_candidates(
        campaign.id,
        [CampaignCandidateCreate(candidate_name=name, email=email, phone=phone)],
        session,
    )
    candidate = added[0]
    candidate.survey_status = "non_responder"
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def _attempt_for(session, campaign, candidate):
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id=candidate.id,
        phone=candidate.phone, attempt_number=1, status="calling",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


# --- opt-out must follow the person, not the campaign row ------------------

def test_opt_out_applies_to_the_person_across_campaigns(session):
    august = _campaign(session, "August Intake")
    september = _campaign(session, "September Intake")
    in_august = _add_person(session, august, email="ada@x.com", phone="+2348011111111")
    in_september = _add_person(
        session, september, email="ada@x.com", phone="+2348011111111"
    )
    attempt = _attempt_for(session, august, in_august)

    record_opt_out(session, august.id, attempt.id)

    # The same human, in a different campaign, is suppressed too.
    session.refresh(in_september)
    assert in_september.opted_out_call is True
    assert build_outbound_call_queue(september.id, session) == []


def test_reuploading_an_opted_out_person_keeps_them_suppressed(session):
    august = _campaign(session, "August Intake")
    person = _add_person(session, august, email="ada@x.com", phone="+2348011111111")
    attempt = _attempt_for(session, august, person)
    record_opt_out(session, august.id, attempt.id)

    # Next month's spreadsheet is uploaded fresh, with no opt-out marked.
    october = _campaign(session, "October Intake")
    added = _add_person(session, october, email="ada@x.com", phone="+2348011111111")

    assert added.opted_out_call is True
    assert build_outbound_call_queue(october.id, session) == []


def test_opt_out_matches_on_phone_when_email_is_missing(session):
    a = _campaign(session, "A")
    b = _campaign(session, "B")
    in_a = _add_person(session, a, email=None, phone="+2348012222222")
    in_b = _add_person(session, b, email=None, phone="+2348012222222")
    record_opt_out(session, a.id, _attempt_for(session, a, in_a).id)

    session.refresh(in_b)
    assert in_b.opted_out_call is True


def test_opt_out_does_not_suppress_unrelated_people(session):
    a = _campaign(session, "A")
    opted = _add_person(session, a, email="ada@x.com", phone="+2348013333333")
    other = _add_person(
        session, a, email="bola@x.com", phone="+2348014444444", name="Bola"
    )
    record_opt_out(session, a.id, _attempt_for(session, a, opted).id)

    session.refresh(other)
    assert other.opted_out_call is False


def test_opt_out_without_a_candidate_row_is_an_error_not_a_false_success(session):
    campaign = _campaign(session, "Orphaned")
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id="does-not-exist",
        phone="+2348010000000", attempt_number=1, status="calling",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    # Must not report success when nothing was suppressed.
    with pytest.raises(LookupError):
        record_opt_out(session, campaign.id, attempt.id)


# --- an answer must never be silently overwritten --------------------------

def test_different_questions_sharing_a_position_are_both_kept(session):
    campaign = _campaign(session, "C")
    candidate = _add_person(session, campaign, email="a@x.com", phone="+2348015555555")
    attempt = _attempt_for(session, campaign, candidate)

    record_survey_answer(
        session, campaign.id, attempt.id, "How satisfied were you?", "Very", position=1
    )
    # An agent reusing position=1 for a different question must not destroy it.
    record_survey_answer(
        session, campaign.id, attempt.id, "Would you recommend us?", "No", position=1
    )

    rows = session.exec(select(OutboundSurveyAnswer)).all()
    assert len(rows) == 2
    assert {r.answer for r in rows} == {"Very", "No"}


def test_same_question_and_position_still_corrects_in_place(session):
    campaign = _campaign(session, "C")
    candidate = _add_person(session, campaign, email="a@x.com", phone="+2348016666666")
    attempt = _attempt_for(session, campaign, candidate)

    for answer in ("Very", "Actually, somewhat"):
        record_survey_answer(
            session, campaign.id, attempt.id, "How satisfied?", answer, position=1
        )

    rows = session.exec(select(OutboundSurveyAnswer)).all()
    assert len(rows) == 1
    assert rows[0].answer == "Actually, somewhat"


# --- a stuck call must not park the campaign forever -----------------------

def test_orchestration_sweeps_stale_calls_so_retries_can_fire(session, monkeypatch):
    monkeypatch.setattr(orch_jobs, "engine", session.get_bind())
    campaign = _campaign(session, "Stuck")
    campaign.status = "outbound_calling"
    session.add(campaign)
    candidate = _add_person(session, campaign, email="a@x.com", phone="+2348017777777")
    # A call placed long ago that never connected: no webhook ever arrives.
    stale = OutboundCallAttempt(
        campaign_id=campaign.id, candidate_id=candidate.id, phone=candidate.phone,
        attempt_number=1, status="calling",
        started_at=datetime.utcnow() - timedelta(days=3),
    )
    session.add(stale)
    session.commit()

    result = orch_jobs.run_campaign_orchestration_job()

    # The sweep ran and freed the attempt, rather than waiting on it forever.
    session.refresh(stale)
    assert stale.status == "no_answer"
    assert result["swept"]


# --- the agent must never speak the wrong hour -----------------------------

def test_timezone_aware_assessment_time_is_rejected():
    # Silently dropping the offset would store 09:00 and speak "9 AM" to a
    # candidate whose assessment is at 10.
    with pytest.raises(ValueError):
        CampaignCreate(name="x", assessment_at="2026-09-02T09:00:00Z")
    with pytest.raises(ValueError):
        CampaignOutboundUpdate(assessment_at="2026-09-02T09:00:00+01:00")


def test_naive_local_assessment_time_is_accepted():
    created = CampaignCreate(name="x", assessment_at="2026-09-02T10:00:00")
    assert created.assessment_at == datetime(2026, 9, 2, 10, 0)
