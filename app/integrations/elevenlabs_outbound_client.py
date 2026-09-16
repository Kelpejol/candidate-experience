"""Client wrapper for placing outbound candidate calls via the ElevenLabs
conversational AI batch-calling API.

Given an OutboundCallAttempt, builds the dynamic variables the voice agent
needs and submits a single-recipient batch call request to ElevenLabs.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from elevenlabs import (
    ConversationInitiationClientDataRequestInput,
    ElevenLabs,
    OutboundCallRecipient,
)

from app.core.config import get_settings
from app.models.campaign import Campaign
from app.models.outbound_call_attempt import OutboundCallAttempt


def _text(value: Any) -> str:
    """Coerce a value to a clean string for the agent's prompt; None -> ""."""
    return "" if value is None else str(value).strip()


def _format_assessment_at(value: datetime | None) -> str:
    """Human, speakable form of the assessment time, e.g.
    'Monday, 02 September 2026 at 10:00 AM'. Empty string when unset."""
    if value is None:
        return ""
    return value.strftime("%A, %d %B %Y at %I:%M %p")


def _time_of_day_greeting(now: datetime | None = None) -> str:
    """"Good morning" / "Good afternoon" / "Good evening" for right now, in the
    configured local timezone.

    Computed here rather than left to the model: an LLM has no reliable clock,
    and getting the opening line wrong (an evening call greeted "good
    morning") is the first thing a candidate would notice. Placed calls dial
    out within moments of this being computed, so the small gap between queue
    and answer is never enough to cross a boundary.
    """
    settings = get_settings()
    local_now = (now or datetime.now(ZoneInfo("UTC"))).astimezone(
        ZoneInfo(settings.job_timezone)
    )
    hour = local_now.hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def build_outbound_dynamic_variables(
    attempt: OutboundCallAttempt, campaign: Campaign | None = None
) -> dict[str, str]:
    """Build the dynamic variable payload passed to the ElevenLabs voice agent.

    Maps fields from an OutboundCallAttempt — and, when supplied, the campaign's
    outbound call context (the outreach script's [brackets]) — into the flat
    dict of variables the agent's prompt references at call time.

    EVERY value is a non-None string. A None here would be interpolated into
    the spoken prompt literally ("Am I speaking with None?"), so optional
    fields become "" and the agent's prompt is written to handle an empty
    value (e.g. it asks to confirm the candidate generically when the name is
    unknown — candidates are often uploaded with only a phone number).
    """
    variables: dict[str, str] = {
        "outbound_attempt_id": _text(attempt.id),
        "campaign_id": _text(attempt.campaign_id),
        "candidate_id": _text(attempt.candidate_id),
        "candidate_name": _text(attempt.candidate_name),
        "candidate_email": _text(attempt.candidate_email),
        "campaign_name": _text(attempt.campaign_name),
        "tool_name": _text(attempt.tool_name),
        "attempt_number": _text(attempt.attempt_number),
        "time_of_day_greeting": _time_of_day_greeting(),
    }

    # Always define the campaign keys. An ABSENT variable is worse than an
    # empty one — the agent's prompt would reference something undefined
    # instead of a blank it can handle.
    variables.update(
        {
            "call_reason": _text(campaign.call_reason) if campaign else "",
            "organization_name": (
                _text(campaign.organization_name or campaign.name) if campaign else ""
            ),
            "assessment_at": (
                _format_assessment_at(campaign.assessment_at) if campaign else ""
            ),
            "assessment_location": (
                _text(campaign.assessment_location) if campaign else ""
            ),
            "practice_test_url": (
                _text(campaign.practice_test_url) if campaign else ""
            ),
            "contact_info": _text(campaign.contact_info) if campaign else "",
        }
    )

    return variables


def create_outbound_call_for_attempt(
    attempt: OutboundCallAttempt, campaign: Campaign | None = None
) -> dict:
    """Submit an outbound call to ElevenLabs for a single OutboundCallAttempt.

    Side effects: makes a network call to the ElevenLabs conversational AI
    "batch calls" endpoint (used here with a single recipient to place one
    call). Raises RuntimeError if required ElevenLabs settings (API key,
    outbound agent id, outbound phone number id) are missing.

    Returns a dict summarizing the created batch call (id, status, and call
    counts) as reported by ElevenLabs.
    """
    settings = get_settings()

    if not settings.elevenlabs_api_key:
        raise RuntimeError("ElevenLabs API key is not configured")

    if not settings.elevenlabs_outbound_agent_id:
        raise RuntimeError("ElevenLabs outbound agent id is not configured")

    if not settings.elevenlabs_outbound_phone_number_id:
        raise RuntimeError("ElevenLabs outbound phone number id is not configured")

    # Bound the request (the SDK default is 240s) so a stalled provider can't
    # hang the worker.
    client = ElevenLabs(api_key=settings.elevenlabs_api_key, timeout=30)
    call_name = f"{attempt.campaign_name or 'Campaign'} - {attempt.phone}"

    # ElevenLabs only exposes calling through the batch-calls API, so a
    # single outbound attempt is submitted as a batch of one recipient.
    #
    # max_retries=0 is deliberate: placing a call is NOT idempotent — if the
    # SDK retried a request that had already dispatched (a 5xx-after-dispatch,
    # or a response lost to a timeout), the candidate would be dialed twice. We
    # would rather fail the attempt and let the (status-gated) retry queue
    # decide, than risk a duplicate live call.
    response = client.conversational_ai.batch_calls.create(
        call_name=call_name,
        agent_id=settings.elevenlabs_outbound_agent_id,
        agent_phone_number_id=settings.elevenlabs_outbound_phone_number_id,
        recipients=[
            OutboundCallRecipient(
                id=attempt.id,
                phone_number=attempt.phone,
                conversation_initiation_client_data=ConversationInitiationClientDataRequestInput(
                    user_id=attempt.candidate_id,
                    dynamic_variables=build_outbound_dynamic_variables(attempt, campaign),
                ),
            )
        ],
        target_concurrency_limit=settings.elevenlabs_outbound_concurrency_limit,
        request_options={"max_retries": 0},
    )

    return {
        "batch_call_id": response.id,
        "status": str(response.status),
        "total_calls_scheduled": response.total_calls_scheduled,
        "total_calls_dispatched": response.total_calls_dispatched,
    }
