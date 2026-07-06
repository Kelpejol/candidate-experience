



def test_handoff_returns_twiml_xml(client):
    response = client.post("/voice/handoff?external_call_id=ca1234567890abcdef")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Response>" in response.text
    assert "<Say>" in response.text
    assert "<Dial" in response.text
    assert 'answerOnBridge="true"' in response.text
    assert 'record="record-from-answer-dual"' in response.text



def test_handoff_twiml_includes_status_action_url(client):
    response = client.post("/voice/handoff?external_call_id=ca1234567890abcdef")
    assert response.status_code == 200
    assert 'action="http://localhost:8000/voice/handoff/status?external_call_id=ca1234567890abcdef"' in response.text
    assert 'method="POST"' in response.text



def test_handoff_status_accepts_twilio_form_payload(client):
    client.post("/call-records", json={
        "external_call_id": "ca1234567890abcdef",
        "direction": "inbound",
        "disposition": "answered_by_ai"
    })
    response = client.post(
        "/voice/handoff/status?external_call_id=ca1234567890abcdef",
        data={
            "DialCallStatus": "completed",
            "DialCallSid": "CA1234567890abcdef",
            "DialCallDuration": "120",
            "DialBridged": "true",
            "RecordingUrl": "https://api.twilio.com/recordings/RE1234567890abcdef",
        },
    )
    detail_response = client.get("/call-records/ca1234567890abcdef")
    assert detail_response.json()["disposition"] == "handed_off_to_human"
    assert detail_response.json()["recording_url"] == "https://api.twilio.com/recordings/RE1234567890abcdef"
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    assert data["dial_call_status"] == "completed"
    assert data["dial_call_sid"] == "CA1234567890abcdef"
    assert data["dial_call_duration"] == 120
    assert data["dial_bridged"] is True
    assert data["recording_url"] == "https://api.twilio.com/recordings/RE1234567890abcdef"
    assert data["external_call_id"] == "ca1234567890abcdef"




def test_handoff_status_failed_does_not_mark_call_as_handed_off(client):
    client.post(
        "/call-records",
        json={
            "external_call_id": "failed_handoff_call",
            "direction": "inbound",
            "disposition": "answered_by_ai",
        },
    )

    response = client.post(
        "/voice/handoff/status?external_call_id=failed_handoff_call",
        data={
            "DialCallStatus": "failed",
            "DialCallSid": "CAfailed",
            "DialCallDuration": "0",
            "DialBridged": "false",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["dial_call_status"] == "failed"
    assert data["dial_bridged"] is False

    detail_response = client.get("/call-records/failed_handoff_call")
    detail_data = detail_response.json()

    assert detail_data["disposition"] == "handoff_failed"
    assert detail_data["handoff_status"] == "failed"
    assert detail_data["handoff_bridged"] is False


def test_handoff_status_returns_404_for_unknown_call_record(client):
    response = client.post(
        "/voice/handoff/status?external_call_id=unknown_call",
        data={
            "DialCallStatus": "completed",
            "DialCallSid": "CAunknown",
            "DialCallDuration": "30",
            "DialBridged": "true",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Call record not found"