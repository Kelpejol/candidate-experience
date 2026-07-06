from datetime import datetime, timedelta, timezone

from app.schemas.call_record import CallRecordCreate


def map_post_call_transcription_to_call_record(payload: dict) -> CallRecordCreate:
    data = payload["data"]
    metadata = data.get("metadata") or {}
    analysis = data.get("analysis") or {}

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

        if duration_secs is not None:
            end_time = start_time + timedelta(seconds=duration_secs)

    transcript_lines = []

    for turn in data.get("transcript", []):
        role = turn.get("role", "unknown")
        message = turn.get("message")

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