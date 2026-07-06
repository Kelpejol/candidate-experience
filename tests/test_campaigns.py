def test_create_campaign(client):
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


def test_add_and_list_campaign_candidates(client):
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
            "opted_out_call": True,
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


def test_add_candidates_to_missing_campaign_returns_404(client):
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
            },
        ],
    )

    candidates = add_response.json()

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

    assert build_response.status_code == 201
    attempts = build_response.json()
    assert len(attempts) == 1
    assert attempts[0]["candidate_id"] == eligible_candidate_id
    assert attempts[0]["phone"] == "+2348012345678"
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["status"] == "queued"

    attempts_response = client.get(f"/campaigns/{campaign_id}/outbound/attempts")

    assert attempts_response.status_code == 200
    assert len(attempts_response.json()) == 1



def test_build_outbound_queue_is_idempotent(client):
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