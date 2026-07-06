from datetime import datetime, timezone

from app.services.elevenlabs_webhook_mapper import (
    map_post_call_transcription_to_call_record,
)


def test_maps_post_call_transcription_to_call_record():
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_123",
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
                "transcript_summary": "Candidate asked about test access.",
            },
            "transcript": [
                {
                    "role": "agent",
                    "message": "Hello, how can I help?",
                },
                {
                    "role": "user",
                    "message": "I cannot access my test link.",
                },
            ],
        },
    }

    record = map_post_call_transcription_to_call_record(payload)

    assert record.external_call_id == "conv_123"
    assert record.direction == "inbound"
    assert record.disposition == "answered_by_ai"
    assert record.issue_summary == "Candidate asked about test access."
    assert record.transcription == (
        "agent: Hello, how can I help?\n"
        "user: I cannot access my test link."
    )
    assert record.call_start_time == datetime.fromtimestamp(
        1782924106,
        tz=timezone.utc,
    )
    assert record.call_end_time == datetime.fromtimestamp(
        1782924124,
        tz=timezone.utc,
    )


def test_maps_transfer_to_number_as_handed_off_to_human():
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_456",
            "metadata": {
                "features_usage": {
                    "transfer_to_number": {
                        "used": True,
                    },
                },
            },
            "analysis": {},
            "transcript": [],
        },
    }

    record = map_post_call_transcription_to_call_record(payload)

    assert record.disposition == "handed_off_to_human"