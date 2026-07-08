"""Maps ElevenLabs conversational-voice-agent webhook payloads to internal schemas.

Currently handles the "post_call_transcription" webhook event, translating
its nested metadata/analysis/transcript structure into a CallRecordCreate
ready for persistence.
"""

from datetime import datetime, timedelta, timezone

from app.schemas.call_record import CallRecordCreate


def map_post_call_transcription_to_call_record(payload: dict) -> CallRecordCreate:
    """
    Convert an ElevenLabs "post_call_transcription" webhook payload into a
    CallRecordCreate.

    Derives call_start_time/call_end_time from the unix start time and
    duration in the payload's metadata, flattens the transcript turns into
    a single "role: message" text block, and sets disposition based on
    whether the call was transferred to a human. Does not perform any I/O;
    callers are responsible for persisting the returned record.
    """
    data = payload["data"]
    metadata = data.get("metadata") or {}
    analysis = data.get("analysis") or {}

    # Nested, deeply-optional field: whether the agent transferred the call
    # to a human during the conversation. Defaults to False if any level
    # of the path is missing from the payload.
    transfer_used = (
        metadata
        .get("features_usage", {})
        .get("transfer_to_number", {})
        .get("used", False)
    )

    start_time = None
    end_time = None

    start_unix = metadata.get("start_time_unix_secs")
    duration_secs = metadata.get("call_duration_secs")

    if start_unix is not None:
        start_time = datetime.fromtimestamp(start_unix, tz=timezone.utc)

        # end_time is only derivable if we also have a duration.
        if duration_secs is not None:
            end_time = start_time + timedelta(seconds=duration_secs)

    transcript_lines = []

    for turn in data.get("transcript", []):
        role = turn.get("role", "unknown")
        message = turn.get("message")

        # Skip turns with no message (e.g. tool-call-only turns) rather
        # than emitting empty lines.
        if message:
            transcript_lines.append(f"{role}: {message}")

    return CallRecordCreate(
        external_call_id=data["conversation_id"],
        direction="inbound",
        disposition="handed_off_to_human" if transfer_used else "answered_by_ai",
        issue_summary=analysis.get("transcript_summary"),
        transcription="\n".join(transcript_lines) or None,
        call_start_time=start_time,
        call_end_time=end_time,
    )