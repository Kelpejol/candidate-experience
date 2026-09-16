"""Maps ElevenLabs conversational-voice-agent webhook payloads to internal schemas.

Handles the "post_call_transcription" webhook event. A call is recognized as
an **outbound campaign call** when the payload echoes back an
`outbound_attempt_id` in its conversation dynamic variables (we set those when
placing the call — see elevenlabs_outbound_client). Otherwise it's treated as
an inbound support call. The parsed result feeds both a CallRecord (the call
log) and, for outbound calls, the originating OutboundCallAttempt.
"""

from datetime import datetime, timedelta, timezone

from app.core.vocabulary import is_tool_allowed
from app.schemas.call_record import CallRecordCreate


def extract_dynamic_variables(payload: dict) -> dict:
    """Return the conversation dynamic variables echoed back in the webhook.

    ElevenLabs includes the variables we passed at call time under
    data.conversation_initiation_client_data.dynamic_variables. Returns an
    empty dict when absent (e.g. an inbound call started no client data).
    """
    data = payload.get("data") or {}
    client_data = data.get("conversation_initiation_client_data") or {}
    return client_data.get("dynamic_variables") or {}


def get_outbound_context(payload: dict) -> dict | None:
    """Return the outbound-call context if this webhook is for a campaign call.

    Detected by the presence of `outbound_attempt_id` in the dynamic
    variables. Returns None for inbound calls.
    """
    variables = extract_dynamic_variables(payload)
    attempt_id = variables.get("outbound_attempt_id")
    if not attempt_id:
        return None
    return {
        "outbound_attempt_id": attempt_id,
        "campaign_id": variables.get("campaign_id"),
        "candidate_id": variables.get("candidate_id"),
        "candidate_name": variables.get("candidate_name"),
        "candidate_email": variables.get("candidate_email"),
        "campaign_name": variables.get("campaign_name"),
        "tool_name": variables.get("tool_name"),
    }


def transfer_to_human_used(payload: dict) -> bool:
    """Whether the agent transferred the call to a human during the call.

    Uses `or {}` rather than a dict default at every hop: providers send a
    present-but-null key for an absent optional field, and `.get(k, {})` only
    substitutes when the key is MISSING — a null would blow up on `.get`.
    """
    metadata = (payload.get("data") or {}).get("metadata") or {}
    features = metadata.get("features_usage") or {}
    transfer = features.get("transfer_to_number") or {}
    return bool(transfer.get("used", False))


def survey_completed_on_call(payload: dict) -> bool:
    """Whether the agent's data collection indicates the candidate completed
    the survey by voice.

    Reads data.analysis.data_collection_results and treats any survey/response
    field with a truthy value as completion. The exact field name depends on
    the outbound survey agent's data-collection config; this matches on the
    field *meaning* rather than a hardcoded key so it keeps working as that
    config is finalized.
    """
    analysis = (payload.get("data") or {}).get("analysis") or {}
    results = analysis.get("data_collection_results") or {}
    for key, value in results.items():
        if "survey" in key.lower() or "respond" in key.lower():
            resolved = value.get("value") if isinstance(value, dict) else value
            if resolved in (True, "true", "True", "yes", "completed", "complete"):
                return True
    return False


def flatten_transcript(payload: dict) -> str | None:
    """Flatten the transcript turns into a single "role: message" text block.

    Tolerates a null transcript (a call that ended with no turns sends
    `"transcript": null`, which a bare `.get(k, [])` would return as None and
    then fail to iterate) and non-dict turns.
    """
    lines = []
    for turn in (payload.get("data") or {}).get("transcript") or []:
        if not isinstance(turn, dict):
            continue
        message = turn.get("message")
        if message:
            lines.append(f"{turn.get('role', 'unknown')}: {message}")
    return "\n".join(lines) or None


def extract_candidate_phone(payload: dict) -> str | None:
    """The candidate's phone number for this call.

    For an outbound call the number we dialled is authoritative; for an inbound
    call it's the caller's number from the telephony metadata. Without this,
    CSAT can never resolve a caller to a candidate and every auto-created
    invitation stalls at pending_contact_lookup.
    """
    data = payload.get("data") or {}
    metadata = data.get("metadata") or {}
    phone_call = metadata.get("phone_call") or {}

    variables = extract_dynamic_variables(payload)
    candidates = [
        variables.get("candidate_phone"),
        phone_call.get("external_number"),
        phone_call.get("from_number"),
        phone_call.get("to_number"),
    ]
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return None


def extract_recording_url(payload: dict) -> str | None:
    """Best-effort recording URL from the payload (config-dependent; may be None)."""
    data = payload.get("data") or {}
    metadata = data.get("metadata") or {}
    return metadata.get("recording_url") or data.get("recording_url")


def call_times(payload: dict) -> tuple[datetime | None, datetime | None]:
    """Derive (start, end) from the unix start time and duration in metadata."""
    metadata = (payload.get("data") or {}).get("metadata") or {}
    start_unix = metadata.get("start_time_unix_secs")
    duration = metadata.get("call_duration_secs")
    if start_unix is None:
        return None, None
    # A string or out-of-range timestamp must not lose the whole call record.
    try:
        start = datetime.fromtimestamp(float(start_unix), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None, None
    try:
        end = start + timedelta(seconds=float(duration)) if duration is not None else None
    except (TypeError, ValueError, OverflowError):
        end = None
    return start, end


def call_disposition(payload: dict) -> str:
    """CallRecord disposition for a connected call (log-level outcome)."""
    return "handed_off_to_human" if transfer_to_human_used(payload) else "answered_by_ai"


def outbound_attempt_status(payload: dict) -> str:
    """OutboundCallAttempt status for a connected outbound call (pipeline-level).

    - transferred to a human  -> handed_off_to_human
    - survey completed by voice -> responded_by_call
    - otherwise (connected)    -> answered
    """
    if transfer_to_human_used(payload):
        return "handed_off_to_human"
    if survey_completed_on_call(payload):
        return "responded_by_call"
    return "answered"


def map_post_call_transcription_to_call_record(payload: dict) -> CallRecordCreate:
    """Convert a post_call_transcription webhook payload into a CallRecordCreate.

    Sets direction to "outbound" when the call carries an outbound attempt
    context (and enriches candidate/campaign/tool from it), else "inbound".
    Does no I/O — callers persist the returned record.
    """
    data = payload.get("data") or {}
    analysis = data.get("analysis") or {}
    context = get_outbound_context(payload)
    start_time, end_time = call_times(payload)

    tool_name = context.get("tool_name") if context else None
    if tool_name and not is_tool_allowed(tool_name):
        tool_name = None  # never let an unexpected agent value fail validation

    return CallRecordCreate(
        external_call_id=data.get("conversation_id"),
        direction="outbound" if context else "inbound",
        candidate_name=context.get("candidate_name") if context else None,
        candidate_phone=extract_candidate_phone(payload),
        campaign_name=context.get("campaign_name") if context else None,
        tool_name=tool_name,
        disposition=call_disposition(payload),
        issue_summary=analysis.get("transcript_summary"),
        transcription=flatten_transcript(payload),
        recording_url=extract_recording_url(payload),
        call_start_time=start_time,
        call_end_time=end_time,
    )
