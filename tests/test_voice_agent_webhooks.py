

import hashlib
import hmac
import json
import time


from app.core.config import get_settings

def post_signed_elevenlabs_webhook(client, payload, secret="test_webhook_secret"):
    get_settings.cache_clear()

    raw_body = json.dumps(payload, separators=(",", ":"))
    timestamp = str(int(time.time()))

    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{raw_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    signature = f"t={timestamp},v0={digest}"

    return client.post(
        "/webhooks/voice-agent/elevenlabs",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "elevenlabs-signature": signature,
        },
    )


def test_voice_agent_call_ended_creates_call_record(client):
    payload = {
        "external_call_id": "ca1234567890abcdef",
        "direction": "inbound",
        "candidate_phone": "+1234567890",
        "candidate_name": "John Doe",
        "tool_name": "FOT",
        "campaign_name": "Graduate Test",
        "disposition": "answered_by_ai",
        "issue_summary": "Candidate could not access test link",
        "transcription": "Candidate asked about test access.",
        "recording_url": "https://api.twilio.com/recordings/RE1234567890abcdef",
       
    }
    response = client.post("/webhooks/voice-agent/call-ended", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True

    detail_response = client.get(f"/call-records/{payload['external_call_id']}")
    assert detail_response.status_code == 200
    detail_data = detail_response.json()
    assert detail_data["external_call_id"] == payload["external_call_id"]
    assert detail_data["candidate_name"] == payload["candidate_name"]
    assert detail_data["candidate_phone"] == payload["candidate_phone"]
    assert detail_data["tool_name"] == payload["tool_name"]
    assert detail_data["disposition"] == payload["disposition"]



def test_voice_agent_call_ended_is_idempotent_for_duplicate_call_record(client):
    payload = {
        "external_call_id": "agent_call_001",
        "direction": "inbound",
        "disposition": "answered_by_ai",
    }

    first_response = client.post("/webhooks/voice-agent/call-ended", json=payload)
    second_response = client.post("/webhooks/voice-agent/call-ended", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["accepted"] is True
    assert second_response.json()["message"] == "Voice agent call record already exists"





def test_elevenlabs_webhook_creates_call_record(client, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test_api_key")
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_test_001",
            "metadata": {
                "start_time_unix_secs": 1782924106,
                "call_duration_secs": 18,
                "features_usage": {
                    "transfer_to_number": {
                        "used": False,
                    },
                },
            },
            "analysis": {
                "transcript_summary": "Candidate asked about FOT exam.",
            },
            "transcript": [
                {
                    "role": "agent",
                    "message": "Hello, how can I help?",
                },
                {
                    "role": "user",
                    "message": "I want to ask about the FOT exam.",
                },
            ],
        },
    }

    response = post_signed_elevenlabs_webhook(client, payload)

    assert response.status_code == 200
    assert response.json()["received"] is True
    assert response.json()["stored"] is True
    assert response.json()["external_call_id"] == "conv_test_001"

    detail_response = client.get("/call-records/conv_test_001")

    assert detail_response.status_code == 200
    detail_data = detail_response.json()
    assert detail_data["external_call_id"] == "conv_test_001"
    assert detail_data["disposition"] == "answered_by_ai"
    assert detail_data["issue_summary"] == "Candidate asked about FOT exam."
    assert detail_data["transcription"] == (
        "agent: Hello, how can I help?\n"
        "user: I want to ask about the FOT exam."
    )




def test_elevenlabs_webhook_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test_api_key")
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_duplicate_001",
            "metadata": {},
            "analysis": {},
            "transcript": [],
        },
    }

    first_response = post_signed_elevenlabs_webhook(client, payload)
    second_response = post_signed_elevenlabs_webhook(client, payload)
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["stored"] is True
    assert second_response.json()["stored"] is False
    assert second_response.json()["message"] == "Call record already exists"