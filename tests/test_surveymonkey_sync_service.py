from app.services.surveymonkey_sync_service import (
    build_candidate_survey_updates,
    summarize_collector_responses,
)

class FakeCandidate:
    def __init__(self, id: str, email: str | None):
        self.id = id
        self.email = email

def test_summarize_collector_responses_counts_completed_partial_and_non_responders():
    recipients = [
        {"id": "recipient_1", "email": "one@example.com"},
        {"id": "recipient_2", "email": "two@example.com"},
        {"id": "recipient_3", "email": "three@example.com"},
        {"id": "recipient_4", "email": "four@example.com"},
    ]

    responses = [
        {
            "id": "response_1",
            "collector_id": "collector_1",
            "recipient_id": "recipient_1",
            "response_status": "completed",
        },
        {
            "id": "response_2",
            "collector_id": "collector_1",
            "recipient_id": "recipient_2",
            "response_status": "partial",
        },
        {
            "id": "response_3",
            "collector_id": "another_collector",
            "recipient_id": "recipient_3",
            "response_status": "completed",
        },
    ]

    summary = summarize_collector_responses(
        recipients=recipients,
        responses=responses,
        collector_id="collector_1",
    )

    assert summary["total_recipients"] == 4
    assert summary["completed"] == 1
    assert summary["partial"] == 1
    assert summary["non_responders"] == 2
    assert summary["responded_by_recipient_id"]["recipient_1"]["id"] == "response_1"
    assert summary["responded_by_recipient_id"]["recipient_2"]["id"] == "response_2"
    assert summary["non_responder_recipients"] == [
        {"id": "recipient_3", "email": "three@example.com"},
        {"id": "recipient_4", "email": "four@example.com"},
    ]



def test_build_candidate_survey_updates_matches_candidates_by_email():
    candidates = [
        FakeCandidate(id="candidate_1", email="One@Example.com"),
        FakeCandidate(id="candidate_2", email="two@example.com"),
        FakeCandidate(id="candidate_3", email="missing@example.com"),
    ]

    recipients = [
        {"id": "recipient_1", "email": "one@example.com"},
        {"id": "recipient_2", "email": "two@example.com"},
    ]

    response_summary = {
        "responded_by_recipient_id": {
            "recipient_1": {
                "id": "response_1",
                "response_status": "completed",
                "date_modified": "2026-06-20T16:05:07+00:00",
            }
        }
    }

    updates = build_candidate_survey_updates(
        candidates=candidates,
        recipients=recipients,
        response_summary=response_summary,
    )

    assert updates == [
        {
            "candidate_id": "candidate_1",
            "surveymonkey_recipient_id": "recipient_1",
            "surveymonkey_response_id": "response_1",
            "surveymonkey_response_status": "completed",
            "survey_status": "responded",
            "survey_responded_at": "2026-06-20T16:05:07+00:00",
        },
        {
            "candidate_id": "candidate_2",
            "surveymonkey_recipient_id": "recipient_2",
            "surveymonkey_response_id": None,
            "surveymonkey_response_status": None,
            "survey_status": "non_responder",
            "survey_responded_at": None,
        },
    ]