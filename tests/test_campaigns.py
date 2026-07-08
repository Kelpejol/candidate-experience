"""Integration tests for the campaigns API and related services.

Exercises campaign creation and status transitions, candidate
upload/listing/status updates, non-responder filtering, the outbound
call queue (build/retry/idempotency/max-attempts), SurveyMonkey
response syncing, outbound call execution, and the async job
enqueue endpoints, using the FastAPI test client against an
in-memory SQLite database (see conftest.py's `client`/`session`
fixtures).
"""

from datetime import datetime


def test_create_campaign(client):
    """Verifies that creating a campaign returns the submitted fields and defaults to 'draft' status."""
    payload = {
        "name": "Graduate Aptitude Test Survey",
        "tool_name": "FOT",
        "survey_id": "survey_001",
        "surveymonkey_collector_id": "collector_001",
        "response_wait_hours": 24,
    }

    response = client.post("/campaigns", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == payload["name"]
    assert data["tool_name"] == payload["tool_name"]
    assert data["survey_id"] == payload["survey_id"]
    assert data["surveymonkey_collector_id"] == payload["surveymonkey_collector_id"]
    assert data["status"] == "draft"


def test_update_campaign_status_sets_survey_sent_timestamp(client):
    """Verifies that transitioning a campaign's status to 'survey_sent' stamps survey_sent_at."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    response = client.patch(
        f"/campaigns/{campaign_id}/status",
        json={"status": "survey_sent"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "survey_sent"
    assert data["survey_sent_at"] is not None


def test_update_campaign_status_rejects_invalid_status(client):
    """Verifies that patching a campaign with a status value outside the allowed enum returns 422."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    response = client.patch(
        f"/campaigns/{campaign_id}/status",
        json={"status": "unknown"},
    )

    assert response.status_code == 422


def test_add_and_list_campaign_candidates(client):
    """Verifies that candidates added to a campaign get correct default statuses and are returned by the list endpoint in insertion order."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    candidates = [
        {
            "candidate_name": "Ada Lovelace",
            "email": "ada@example.com",
            "phone": "+2348012345678",
            "tool_name": "FOT",
            "campaign_name": "Graduate Aptitude Test",
            "external_candidate_id": "cand_001",
        },
        {
            "candidate_name": "Grace Hopper",
            "email": "grace@example.com",
            "phone": "+2348098765432",
            "tool_name": "FOT",
            "campaign_name": "Graduate Aptitude Test",
            "external_candidate_id": "cand_002",
            "opted_out_call": True,  # candidate has opted out of outbound calls
        },
    ]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=candidates,
    )

    assert add_response.status_code == 201
    added = add_response.json()
    assert len(added) == 2
    assert added[0]["candidate_name"] == "Ada Lovelace"
    assert added[0]["survey_status"] == "not_sent"
    assert added[0]["call_status"] == "not_queued"
    assert added[0]["surveymonkey_recipient_id"] is None
    assert added[0]["surveymonkey_response_id"] is None
    assert added[0]["surveymonkey_response_status"] is None
    assert added[0]["survey_responded_at"] is None
    assert added[1]["opted_out_call"] is True

    list_response = client.get(f"/campaigns/{campaign_id}/candidates")

    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 2
    assert listed[0]["external_candidate_id"] == "cand_001"
    assert listed[1]["external_candidate_id"] == "cand_002"


def test_add_campaign_candidates_updates_campaign_status_to_uploaded(client):
    """Verifies that adding candidates to a campaign advances its status from 'draft' to 'uploaded'."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )

    campaign_detail_response = client.get(f"/campaigns/{campaign_id}")

    assert campaign_detail_response.status_code == 200
    assert campaign_detail_response.json()["status"] == "uploaded"

def test_add_candidates_to_missing_campaign_returns_404(client):
    """Verifies that uploading candidates to a nonexistent campaign id returns 404."""
    response = client.post(
        "/campaigns/missing/candidates",
        json=[
            {
                "candidate_name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+2348012345678",
            }
        ],
    )

    assert response.status_code == 404




def test_update_campaign_candidate_survey_status(client):
    """Verifies that patching a candidate's survey status persists and is reflected in the response."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    update_response = client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={
            "survey_status": "partial_response",
        },
    )

    assert update_response.status_code == 200
    data = update_response.json()
    assert data["id"] == candidate_id
    assert data["survey_status"] == "partial_response"




def test_update_campaign_candidate_survey_status_rejects_invalid_status(client):
    """Verifies that patching a candidate's survey status with an unrecognized value returns 422."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    response = client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={
            "survey_status": "unknown",
        },
    )

    assert response.status_code == 422





def test_list_eligible_non_responders_excludes_responders_opted_out_and_missing_phone(client):
    """Verifies that the non-responders listing only includes non-responders with a phone who have not opted out of calls."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Already Responded",
                "email": "responded@example.com",
                "phone": "+2348023456789",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Opted Out",
                "email": "opted@example.com",
                "phone": "+2348034567890",
                "tool_name": "FOT",
                "opted_out_call": True,
            },
            {
                "candidate_name": "No Phone",
                "email": "nophone@example.com",
                "tool_name": "FOT",
                # no "phone" field, so this candidate is uncallable
            },
        ],
    )

    candidates = add_response.json()

    # All three exclusions below are set to "non_responder" so that survey
    # status alone cannot explain their exclusion from the eligible list.
    statuses = {
        "Eligible Candidate": "non_responder",
        "Already Responded": "responded",
        "Opted Out": "non_responder",
        "No Phone": "non_responder",
    }

    for candidate in candidates:
        client.patch(
            f"/campaigns/{campaign_id}/candidates/{candidate['id']}/survey-status",
            json={
                "survey_status": statuses[candidate["candidate_name"]],
            },
        )

    response = client.get(f"/campaigns/{campaign_id}/non-responders")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["candidate_name"] == "Eligible Candidate"




def test_build_outbound_queue_creates_attempts_for_eligible_non_responders_only(client):
    """Verifies that building the outbound call queue creates a first-attempt entry only for eligible non-responders and moves the campaign to 'outbound_calling'."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Already Responded",
                "email": "responded@example.com",
                "phone": "+2348023456789",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Opted Out",
                "email": "opted@example.com",
                "phone": "+2348034567890",
                "tool_name": "FOT",
                "opted_out_call": True,
            },
        ],
    )
    candidates = add_response.json()

    statuses = {
        "Eligible Candidate": "non_responder",
        "Already Responded": "responded",
        "Opted Out": "non_responder",
    }

    eligible_candidate_id = None

    for candidate in candidates:
        if candidate["candidate_name"] == "Eligible Candidate":
            eligible_candidate_id = candidate["id"]

        client.patch(
            f"/campaigns/{campaign_id}/candidates/{candidate['id']}/survey-status",
            json={
                "survey_status": statuses[candidate["candidate_name"]],
            },
        )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    campaign_detail_response = client.get(f"/campaigns/{campaign_id}")
    assert campaign_detail_response.json()["status"] == "outbound_calling"
    assert build_response.status_code == 201
    attempts = build_response.json()
    assert len(attempts) == 1
    assert attempts[0]["candidate_id"] == eligible_candidate_id
    assert attempts[0]["candidate_name"] == "Eligible Candidate"
    assert attempts[0]["candidate_email"] == "eligible@example.com"
    assert attempts[0]["tool_name"] == "FOT"
    assert attempts[0]["phone"] == "+2348012345678"
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["status"] == "queued"

    attempts_response = client.get(f"/campaigns/{campaign_id}/outbound/attempts")

    assert attempts_response.status_code == 200
    assert len(attempts_response.json()) == 1



def test_build_outbound_queue_is_idempotent(client):
    """Verifies that calling build-queue twice for the same campaign does not create duplicate attempts."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={
            "survey_status": "non_responder",
        },
    )

    first_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    second_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert len(first_response.json()) == 1
    assert len(second_response.json()) == 1
    assert first_response.json()[0]["id"] == second_response.json()[0]["id"]

    attempts_response = client.get(f"/campaigns/{campaign_id}/outbound/attempts")

    assert len(attempts_response.json()) == 1


def test_apply_candidate_survey_updates_updates_surveymonkey_fields(client, session):
    """Verifies that apply_candidate_survey_updates persists SurveyMonkey recipient/response fields and parses the response timestamp onto the candidate."""
    from app.services.campaign_service import apply_candidate_survey_updates

    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    updated = apply_candidate_survey_updates(
        campaign_id=campaign_id,
        updates=[
            {
                "candidate_id": candidate_id,
                "surveymonkey_recipient_id": "recipient_1",
                "surveymonkey_response_id": "response_1",
                "surveymonkey_response_status": "completed",
                "survey_status": "responded",
                "survey_responded_at": "2026-06-20T16:05:07+00:00",
            }
        ],
        session=session,
    )

    assert len(updated) == 1
    assert updated[0].surveymonkey_recipient_id == "recipient_1"
    assert updated[0].surveymonkey_response_id == "response_1"
    assert updated[0].surveymonkey_response_status == "completed"
    assert updated[0].survey_status == "responded"
    # input timestamp has a "+00:00" offset but is stored as a naive datetime
    assert updated[0].survey_responded_at == datetime(2026, 6, 20, 16, 5, 7)




def test_sync_campaign_survey_responses_updates_candidates(client, monkeypatch):
    """Verifies that syncing survey responses updates each candidate's survey status/SurveyMonkey fields, marks non-respondents correctly, and advances the campaign to 'outbound_ready'."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
            "survey_id": "survey_1",
            "surveymonkey_collector_id": "collector_1",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Completed Candidate",
                "email": "completed@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Partial Candidate",
                "email": "partial@example.com",
                "phone": "+2348023456789",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "No Response Candidate",
                "email": "missing@example.com",
                "phone": "+2348034567890",
                "tool_name": "FOT",
            },
        ],
    )

    candidate_ids = {
        candidate["candidate_name"]: candidate["id"]
        for candidate in add_response.json()
    }

    class FakeSurveyMonkeyClient:
        """Stand-in for the real SurveyMonkey API client, returning canned recipients/responses instead of making HTTP calls."""

        def __init__(self, base_url: str, access_token: str):
            pass

        def list_all_collector_recipients(self, collector_id: str):
            return [
                {"id": "recipient_1", "email": "completed@example.com"},
                {"id": "recipient_2", "email": "partial@example.com"},
                # recipient_3 has no matching response below, so it should
                # be treated as a non-responder once synced
                {"id": "recipient_3", "email": "missing@example.com"},
            ]

        def list_all_survey_responses_bulk(self, survey_id: str):
            return [
                {
                    "id": "response_1",
                    "collector_id": "collector_1",
                    "recipient_id": "recipient_1",
                    "response_status": "completed",
                    "date_modified": "2026-06-20T16:05:07+00:00",
                },
                {
                    "id": "response_2",
                    "collector_id": "collector_1",
                    "recipient_id": "recipient_2",
                    "response_status": "partial",
                    "date_modified": "2026-06-20T16:10:07+00:00",
                },
            ]

    # Patch the client at the point the sync service imports it, so the
    # service uses the fake instead of calling out to SurveyMonkey.
    monkeypatch.setattr(
    "app.services.surveymonkey_sync_service.SurveyMonkeyClient",
    FakeSurveyMonkeyClient,
)

    response = client.post(f"/campaigns/{campaign_id}/survey/sync-responses")

    assert response.status_code == 200
    data = response.json()
    assert data["total_recipients"] == 3
    assert data["completed"] == 1
    assert data["partial"] == 1
    assert data["non_responders"] == 1
    assert data["updated_candidates"] == 3

    candidates_response = client.get(f"/campaigns/{campaign_id}/candidates")
    candidates = {
        candidate["id"]: candidate
        for candidate in candidates_response.json()
    }

    completed = candidates[candidate_ids["Completed Candidate"]]
    partial = candidates[candidate_ids["Partial Candidate"]]
    missing = candidates[candidate_ids["No Response Candidate"]]
    campaign_detail_response = client.get(f"/campaigns/{campaign_id}")
    campaign_detail = campaign_detail_response.json()

    assert campaign_detail["status"] == "outbound_ready"
    assert campaign_detail["non_responder_checked_at"] is not None

    assert completed["survey_status"] == "responded"
    assert completed["surveymonkey_recipient_id"] == "recipient_1"
    assert completed["surveymonkey_response_id"] == "response_1"
    assert completed["surveymonkey_response_status"] == "completed"

    assert partial["survey_status"] == "partial_response"
    assert partial["surveymonkey_recipient_id"] == "recipient_2"
    assert partial["surveymonkey_response_id"] == "response_2"
    assert partial["surveymonkey_response_status"] == "partial"

    assert missing["survey_status"] == "non_responder"
    assert missing["surveymonkey_recipient_id"] == "recipient_3"
    assert missing["surveymonkey_response_id"] is None
    assert missing["surveymonkey_response_status"] is None



def test_upload_campaign_candidates_from_csv(client):
    """Verifies that uploading a CSV file of candidates creates candidate records with the parsed fields."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    csv_bytes = (
        "candidate_name,email,phone,external_candidate_id\n"
        "Ada Lovelace,ada@example.com,+2348012345678,cand_001\n"
        "Grace Hopper,grace@example.com,+2348098765432,cand_002\n"
    ).encode("utf-8")

    response = client.post(
        f"/campaigns/{campaign_id}/candidates/upload",
        files={
            "file": ("candidates.csv", csv_bytes, "text/csv"),
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data) == 2
    assert data[0]["candidate_name"] == "Ada Lovelace"
    assert data[0]["email"] == "ada@example.com"
    assert data[0]["tool_name"] == "FOT"
    assert data[0]["campaign_name"] == "Graduate Aptitude Test Survey"
    assert data[1]["external_candidate_id"] == "cand_002"



def test_upload_campaign_candidates_rejects_unknown_file_type(client):
    """Verifies that uploading a non-CSV file to the candidates upload endpoint returns 400 with an explanatory message."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    response = client.post(
        f"/campaigns/{campaign_id}/candidates/upload",
        files={
            "file": ("candidates.txt", b"hello", "text/plain"),
        },
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]



def test_update_outbound_attempt_status(client):
    """Verifies that patching an outbound call attempt's status/disposition persists the change and stamps ended_at."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    attempt_id = build_response.json()[0]["id"]

    update_response = client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{attempt_id}/status",
        json={
            "status": "no_answer",
            "disposition": "Candidate did not pick up",
            "elevenlabs_conversation_id": "conv_001",
        },
    )

    assert update_response.status_code == 200
    data = update_response.json()
    assert data["id"] == attempt_id
    assert data["status"] == "no_answer"
    assert data["disposition"] == "Candidate did not pick up"
    assert data["elevenlabs_conversation_id"] == "conv_001"
    assert data["ended_at"] is not None



def test_build_outbound_retry_queue_creates_second_attempt_for_retryable_status(client, session):
    """Verifies that build_outbound_retry_queue creates a second attempt for a candidate whose first attempt ended in a retryable status (e.g. no_answer)."""
    from app.services.campaign_service import build_outbound_retry_queue

    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
        },
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            },
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    first_attempt_id = build_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{first_attempt_id}/status",
        json={
            "status": "no_answer",
            "disposition": "Candidate did not pick up",
        },
    )

    retry_attempts = build_outbound_retry_queue(
        campaign_id=campaign_id,
        session=session,
        max_attempts=3,
    )

    assert len(retry_attempts) == 1
    assert retry_attempts[0].candidate_id == candidate_id
    assert retry_attempts[0].attempt_number == 2
    assert retry_attempts[0].status == "queued"
    assert retry_attempts[0].candidate_email == "eligible@example.com"



def test_build_outbound_retry_queue_endpoint(client):
    """Verifies that the retry-queue endpoint creates a second attempt after a retryable status via the HTTP API."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    first_attempt_id = build_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{first_attempt_id}/status",
        json={"status": "no_answer"},
    )

    retry_response = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue")

    assert retry_response.status_code == 201
    data = retry_response.json()
    assert len(data) == 1
    assert data[0]["candidate_id"] == candidate_id
    assert data[0]["attempt_number"] == 2
    assert data[0]["status"] == "queued"


def test_build_outbound_retry_queue_is_idempotent_while_retry_is_queued(client):
    """Verifies that calling the retry-queue endpoint again while a retry attempt is still queued does not create a duplicate attempt."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    first_attempt_id = build_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{first_attempt_id}/status",
        json={"status": "no_answer"},
    )

    first_retry_response = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue")
    second_retry_response = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue")

    assert first_retry_response.status_code == 201
    assert second_retry_response.status_code == 201
    assert len(first_retry_response.json()) == 1
    assert len(second_retry_response.json()) == 0


def test_build_outbound_retry_queue_respects_max_attempts(client):
    """Verifies that no further retry attempt is created once a candidate has reached the max_attempts limit, even though their latest attempt is still retryable."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Eligible Candidate",
                "email": "eligible@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    first_attempt_id = build_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{first_attempt_id}/status",
        json={"status": "no_answer"},
    )

    first_retry_response = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue")
    second_attempt_id = first_retry_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{second_attempt_id}/status",
        json={"status": "no_answer"},
    )

    second_retry_response = client.post(f"/campaigns/{campaign_id}/outbound/retry-queue")
    third_attempt_id = second_retry_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/outbound/attempts/{third_attempt_id}/status",
        json={"status": "no_answer"},
    )

    # The candidate is now at attempt 3, so with max_attempts=3 no further
    # retry attempt should be generated even though the status is retryable.
    third_retry_response = client.post(
        f"/campaigns/{campaign_id}/outbound/retry-queue",
        params={"max_attempts": 3},
    )

    assert third_retry_response.status_code == 201
    assert third_retry_response.json() == []



def test_get_next_queued_outbound_attempt_returns_oldest_queued_attempt(client):
    """Verifies that the 'next' outbound attempt endpoint returns the earliest-queued attempt across candidates."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "First Candidate",
                "email": "first@example.com",
                "phone": "+2348011111111",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Second Candidate",
                "email": "second@example.com",
                "phone": "+2348022222222",
                "tool_name": "FOT",
            },
        ],
    )

    candidates = add_response.json()

    for candidate in candidates:
        client.patch(
            f"/campaigns/{campaign_id}/candidates/{candidate['id']}/survey-status",
            json={"survey_status": "non_responder"},
        )

    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    attempts = build_response.json()

    response = client.get(f"/campaigns/{campaign_id}/outbound/attempts/next")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == attempts[0]["id"]
    assert data["candidate_name"] == "First Candidate"
    assert data["status"] == "queued"


def test_get_next_queued_outbound_attempt_returns_404_when_none(client):
    """Verifies that the 'next' outbound attempt endpoint returns 404 when the campaign has no queued attempts."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    response = client.get(f"/campaigns/{campaign_id}/outbound/attempts/next")

    assert response.status_code == 404
    assert response.json()["detail"] == "No queued outbound attempt found"



def test_get_campaign_summary_counts_candidates_and_attempts(client):
    """Verifies that the campaign summary endpoint reports correct total/survey-status/call-status/outbound-attempt breakdowns."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Responded Candidate",
                "email": "responded@example.com",
                "phone": "+2348011111111",
                "tool_name": "FOT",
            },
            {
                "candidate_name": "Non Responder Candidate",
                "email": "nonresponder@example.com",
                "phone": "+2348022222222",
                "tool_name": "FOT",
            },
        ],
    )

    candidates = add_response.json()

    for candidate in candidates:
        survey_status = (
            "responded"
            if candidate["candidate_name"] == "Responded Candidate"
            else "non_responder"
        )

        client.patch(
            f"/campaigns/{campaign_id}/candidates/{candidate['id']}/survey-status",
            json={"survey_status": survey_status},
        )

    client.post(f"/campaigns/{campaign_id}/outbound/build-queue")

    response = client.get(f"/campaigns/{campaign_id}/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["campaign_id"] == campaign_id
    assert data["total_candidates"] == 2
    assert data["survey_statuses"] == {
        "responded": 1,
        "non_responder": 1,
    }
    assert data["call_statuses"] == {
        "not_queued": 1,
        "queued": 1,
    }
    assert data["outbound_attempts"] == {
        "queued": 1,
    }


def test_enqueue_campaign_survey_sync_job_reuses_active_job(client, monkeypatch):
    """Verifies that repeated calls to enqueue the survey-sync job reuse the same active job rather than enqueueing duplicates."""
    campaign_response = client.post(
        "/campaigns",
        json={
            "name": "Graduate Aptitude Test Survey",
            "tool_name": "FOT",
            "survey_id": "survey_001",
            "surveymonkey_collector_id": "collector_001",
        },
    )
    campaign_id = campaign_response.json()["id"]

    class FakeStatus:
        """Minimal stand-in for an RQ job status object exposing just `.value`."""

        value = "queued"

    class FakeJob:
        """Minimal stand-in for an RQ Job, enough to satisfy serialize/status calls."""

        id = "job_123"

        def get_status(self, refresh=True):
            return FakeStatus()

    def fake_enqueue_unique_active_job(**kwargs):
        # Confirms the route builds a per-campaign lock key so concurrent
        # sync requests for the same campaign dedupe onto one job.
        assert kwargs["lock_key"] == f"campaign:{campaign_id}:survey-sync-job"
        return FakeJob()

    monkeypatch.setattr(
        "app.api.routes.campaigns.enqueue_unique_active_job",
        fake_enqueue_unique_active_job,
    )

    first_response = client.post(f"/campaigns/{campaign_id}/survey/sync-responses/jobs")
    second_response = client.post(f"/campaigns/{campaign_id}/survey/sync-responses/jobs")

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert first_response.json() == {
        "job_id": "job_123",
        "status": "queued",
    }
    assert second_response.json() == first_response.json()


def test_execute_next_outbound_call_for_campaign_claims_attempt_and_calls_provider(
    client,
    session,
    monkeypatch,
):
    """Verifies that executing the next outbound call claims the queued attempt (moving it to 'calling'), invokes the voice provider, and records the provider result on the attempt/candidate."""
    from app.services.outbound_call_execution_service import (
        execute_next_outbound_call_for_campaign,
    )
    from app.models.campaign_candidate import CampaignCandidate
    from app.models.outbound_call_attempt import OutboundCallAttempt

    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    add_response = client.post(
        f"/campaigns/{campaign_id}/candidates",
        json=[
            {
                "candidate_name": "Outbound Candidate",
                "email": "outbound@example.com",
                "phone": "+2348012345678",
                "tool_name": "FOT",
                "campaign_name": "Graduate Aptitude Test Survey",
            }
        ],
    )
    candidate_id = add_response.json()[0]["id"]

    client.patch(
        f"/campaigns/{campaign_id}/candidates/{candidate_id}/survey-status",
        json={"survey_status": "non_responder"},
    )
    build_response = client.post(f"/campaigns/{campaign_id}/outbound/build-queue")
    attempt_id = build_response.json()[0]["id"]

    def fake_create_outbound_call_for_attempt(attempt):
        # By the time the provider is invoked, the service should already
        # have claimed the attempt (status flipped to "calling").
        assert attempt.id == attempt_id
        assert attempt.status == "calling"
        return {
            "batch_call_id": "batch_123",
            "status": "pending",
            "total_calls_scheduled": 1,
            "total_calls_dispatched": 0,
        }

    monkeypatch.setattr(
        "app.services.outbound_call_execution_service.create_outbound_call_for_attempt",
        fake_create_outbound_call_for_attempt,
    )

    result = execute_next_outbound_call_for_campaign(
        campaign_id=campaign_id,
        session=session,
    )

    attempt = session.get(OutboundCallAttempt, attempt_id)
    candidate = session.get(CampaignCandidate, candidate_id)

    assert result["status"] == "calling"
    assert result["provider_result"]["batch_call_id"] == "batch_123"
    assert attempt.status == "calling"
    assert attempt.disposition == "elevenlabs_batch_call_created"
    assert attempt.summary == "ElevenLabs batch_call_id=batch_123"
    assert candidate.call_status == "calling"


def test_enqueue_next_outbound_call_job_returns_job_id(client, monkeypatch):
    """Verifies that the execute-next outbound call endpoint enqueues a background job and returns its id/status."""
    campaign_response = client.post(
        "/campaigns",
        json={"name": "Graduate Aptitude Test Survey", "tool_name": "FOT"},
    )
    campaign_id = campaign_response.json()["id"]

    class FakeStatus:
        """Minimal stand-in for an RQ job status object exposing just `.value`."""

        value = "queued"

    class FakeJob:
        """Minimal stand-in for an RQ Job, enough to satisfy serialize/status calls."""

        id = "outbound_job_123"

        def get_status(self, refresh=True):
            return FakeStatus()

    def fake_enqueue_unique_active_job(**kwargs):
        # Confirms the route uses a distinct lock key from the survey-sync
        # job so the two job types don't collide/dedupe against each other.
        assert kwargs["lock_key"] == f"campaign:{campaign_id}:outbound-execute-next-job"
        return FakeJob()

    monkeypatch.setattr(
        "app.api.routes.campaigns.enqueue_unique_active_job",
        fake_enqueue_unique_active_job,
    )

    response = client.post(f"/campaigns/{campaign_id}/outbound/execute-next/jobs")

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "outbound_job_123",
        "status": "queued",
    }
