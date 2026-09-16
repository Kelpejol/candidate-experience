import pytest
from sqlmodel import select

from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.models.outbound_survey_answer import OutboundSurveyAnswer
from app.services.outbound_survey_service import (
    attempt_has_survey_answers,
    build_spoken_questions,
    get_campaign_survey_questions,
    record_opt_out,
    record_survey_answer,
)

# A survey-details payload shaped like SurveyMonkey's /surveys/{id}/details,
# exercising every family the mapper handles.
SURVEY_DETAILS = {
    "id": "sv1",
    "title": "Candidate Feedback",
    "pages": [
        {
            "questions": [
                {
                    "family": "presentation",
                    "headings": [{"heading": "Thanks for your time!"}],
                },
                {
                    "family": "single_choice",
                    "headings": [{"heading": "Was your issue <b>resolved</b>?"}],
                    "answers": {
                        "choices": [
                            {"text": "Yes"},
                            {"text": "No"},
                            {"text": "Not sure", "is_na": True},
                        ]
                    },
                },
                {
                    "family": "matrix",
                    "headings": [{"heading": "How satisfied were you?"}],
                    "answers": {
                        "choices": [{"text": "1"}, {"text": "2"}, {"text": "3"}]
                    },
                },
                {
                    "family": "multiple_choice",
                    "headings": [{"heading": "Which channels did you use?"}],
                    "answers": {
                        "choices": [{"text": "Phone"}, {"text": "Email"}]
                    },
                },
                {
                    "family": "open_ended",
                    "headings": [{"heading": "Any other comments?&nbsp;"}],
                    "answers": {},
                },
                {
                    # No heading -> skipped.
                    "family": "single_choice",
                    "headings": [{"heading": ""}],
                    "answers": {"choices": [{"text": "X"}]},
                },
            ]
        }
    ],
}


class _FakeSurveyMonkeyClient:
    def __init__(self, details):
        self._details = details
        self.requested_id = None

    def get_survey_details(self, survey_id):
        self.requested_id = survey_id
        return self._details


def test_build_spoken_questions_maps_every_family():
    questions = build_spoken_questions(SURVEY_DETAILS)

    # Presentation + heading-less questions dropped; positions are 1-based.
    assert [q["position"] for q in questions] == [1, 2, 3, 4]

    resolved, satisfied, channels, comments = questions

    # HTML stripped, N/A choice dropped.
    assert resolved == {
        "position": 1,
        "text": "Was your issue resolved?",
        "type": "choice",
        "options": ["Yes", "No"],
    }
    # matrix rating -> choice with its scale.
    assert satisfied["type"] == "choice"
    assert satisfied["options"] == ["1", "2", "3"]
    # multiple_choice -> multi.
    assert channels["type"] == "multi"
    assert channels["options"] == ["Phone", "Email"]
    # open_ended -> free text, no options, entities cleaned.
    assert comments == {
        "position": 4,
        "text": "Any other comments?",
        "type": "text",
        "options": [],
    }


def test_build_spoken_questions_empty_survey():
    assert build_spoken_questions({}) == []
    assert build_spoken_questions({"pages": []}) == []


def test_get_campaign_survey_questions_uses_campaign_survey(session):
    campaign = Campaign(name="Feedback Run", survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    client = _FakeSurveyMonkeyClient(SURVEY_DETAILS)
    questions = get_campaign_survey_questions(campaign.id, session, client=client)

    assert client.requested_id == "sv1"
    assert questions[0]["text"] == "Was your issue resolved?"


def test_get_campaign_survey_questions_unknown_campaign(session):
    with pytest.raises(LookupError):
        get_campaign_survey_questions("missing", session, client=_FakeSurveyMonkeyClient({}))


def test_get_campaign_survey_questions_no_survey_configured(session):
    campaign = Campaign(name="No Survey", survey_id=None)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    with pytest.raises(ValueError):
        get_campaign_survey_questions(campaign.id, session, client=_FakeSurveyMonkeyClient({}))


# --- answer capture + opt-out (slices 2 & 3) -------------------------------


def _make_attempt(session):
    campaign = Campaign(name="Feedback", survey_id="sv1")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    candidate = CampaignCandidate(
        campaign_id=campaign.id,
        candidate_name="Ada",
        phone="+2348000000000",
        survey_status="non_responder",
        call_status="calling",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    attempt = OutboundCallAttempt(
        campaign_id=campaign.id,
        candidate_id=candidate.id,
        candidate_name="Ada",
        phone="+2348000000000",
        attempt_number=1,
        status="calling",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return campaign, candidate, attempt


def test_record_survey_answer_creates_then_upserts(session):
    campaign, candidate, attempt = _make_attempt(session)

    first = record_survey_answer(
        session, campaign.id, attempt.id, "How satisfied?", "Very",
        position=1, answer_type="choice",
    )
    # Candidate is derived from the attempt, not trusted from the caller.
    assert first.candidate_id == candidate.id

    # Same position -> corrects in place rather than duplicating.
    second = record_survey_answer(
        session, campaign.id, attempt.id, "How satisfied?", "Somewhat", position=1,
    )
    assert second.id == first.id
    assert second.answer == "Somewhat"
    assert second.answer_type == "choice"  # preserved when not re-sent

    rows = session.exec(
        select(OutboundSurveyAnswer).where(
            OutboundSurveyAnswer.outbound_attempt_id == attempt.id
        )
    ).all()
    assert len(rows) == 1


def test_record_survey_answer_validates_and_guards(session):
    campaign, candidate, attempt = _make_attempt(session)
    with pytest.raises(ValueError):
        record_survey_answer(session, campaign.id, attempt.id, "Q", "   ")
    with pytest.raises(LookupError):
        record_survey_answer(session, campaign.id, "missing", "Q", "A")


def test_attempt_has_survey_answers(session):
    campaign, candidate, attempt = _make_attempt(session)
    assert attempt_has_survey_answers(session, attempt.id) is False
    record_survey_answer(session, campaign.id, attempt.id, "Q", "A", position=1)
    assert attempt_has_survey_answers(session, attempt.id) is True


def test_record_opt_out_is_terminal(session):
    campaign, candidate, attempt = _make_attempt(session)
    record_opt_out(session, campaign.id, attempt.id)

    session.refresh(candidate)
    session.refresh(attempt)
    assert candidate.opted_out_call is True
    assert candidate.call_status == "opted_out"
    assert attempt.status == "opted_out"
    assert attempt.disposition == "caller_opted_out"
    assert attempt.ended_at is not None


def test_record_opt_out_unknown_attempt(session):
    campaign, candidate, attempt = _make_attempt(session)
    with pytest.raises(LookupError):
        record_opt_out(session, campaign.id, "missing")
