from datetime import datetime, timezone

from app.services.elevenlabs_webhook_mapper import (
    get_outbound_context,
    map_post_call_transcription_to_call_record,
    outbound_attempt_status,
)


def outbound_payload(dynamic_variables, *, transfer=False, data_collection=None):
    return {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_ob_1",
            "conversation_initiation_client_data": {
                "dynamic_variables": dynamic_variables,
            },
            "metadata": {
                "features_usage": {"transfer_to_number": {"used": transfer}},
            },
            "analysis": {
                "transcript_summary": "Survey call.",
                "data_collection_results": data_collection or {},
            },
            "transcript": [{"role": "agent", "message": "Hi"}],
        },
    }


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


def test_inbound_call_has_no_outbound_context():
    payload = {"type": "post_call_transcription", "data": {"conversation_id": "c"}}
    assert get_outbound_context(payload) is None


def test_outbound_context_detected_from_dynamic_variables():
    payload = outbound_payload({
        "outbound_attempt_id": "att_1",
        "campaign_id": "camp_1",
        "candidate_id": "cand_1",
        "candidate_name": "Aisha Bello",
        "campaign_name": "Dangote PRP",
        "tool_name": "FOT",
    })
    context = get_outbound_context(payload)
    assert context is not None
    assert context["outbound_attempt_id"] == "att_1"
    assert context["campaign_id"] == "camp_1"


def test_outbound_call_sets_direction_and_enriches_from_context():
    payload = outbound_payload({
        "outbound_attempt_id": "att_1",
        "candidate_name": "Aisha Bello",
        "campaign_name": "Dangote PRP",
        "tool_name": "FOT",
    })
    record = map_post_call_transcription_to_call_record(payload)
    assert record.direction == "outbound"
    assert record.candidate_name == "Aisha Bello"
    assert record.campaign_name == "Dangote PRP"
    assert record.tool_name == "FOT"


def test_outbound_status_answered_when_connected_no_transfer():
    payload = outbound_payload({"outbound_attempt_id": "att_1"})
    assert outbound_attempt_status(payload) == "answered"


def test_outbound_status_handed_off_on_transfer():
    payload = outbound_payload({"outbound_attempt_id": "att_1"}, transfer=True)
    assert outbound_attempt_status(payload) == "handed_off_to_human"


def test_outbound_status_responded_by_call_when_survey_completed():
    payload = outbound_payload(
        {"outbound_attempt_id": "att_1"},
        data_collection={"survey_completed": {"value": True}},
    )
    assert outbound_attempt_status(payload) == "responded_by_call"


def test_unknown_agent_tool_value_does_not_break_validation():
    payload = outbound_payload({
        "outbound_attempt_id": "att_1",
        "tool_name": "SomethingWeird",
    })
    record = map_post_call_transcription_to_call_record(payload)
    assert record.tool_name is None  # invalid tool dropped, not a 422