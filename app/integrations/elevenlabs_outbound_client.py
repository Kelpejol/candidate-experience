"""Client wrapper for placing outbound candidate calls via the ElevenLabs
conversational AI batch-calling API.

Given an OutboundCallAttempt, builds the dynamic variables the voice agent
needs and submits a single-recipient batch call request to ElevenLabs.
"""

from typing import Any

from elevenlabs import (
    ConversationInitiationClientDataRequestInput,
    ElevenLabs,
    OutboundCallRecipient,
)

from app.core.config import get_settings
from app.models.outbound_call_attempt import OutboundCallAttempt


def build_outbound_dynamic_variables(attempt: OutboundCallAttempt) -> dict[str, Any]:
    """Build the dynamic variable payload passed to the ElevenLabs voice agent.

    Maps fields from an OutboundCallAttempt into the flat dict of variables
    the agent's conversation prompt/template can reference at call time.
    """
    return {
        "outbound_attempt_id": attempt.id,
        "campaign_id": attempt.campaign_id,
        "candidate_id": attempt.candidate_id,
        "candidate_name": attempt.candidate_name,
        "candidate_email": attempt.candidate_email,
        "campaign_name": attempt.campaign_name,
        "tool_name": attempt.tool_name,
        "attempt_number": attempt.attempt_number,
    }


def create_outbound_call_for_attempt(attempt: OutboundCallAttempt) -> dict:
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

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    call_name = f"{attempt.campaign_name or 'Campaign'} - {attempt.phone}"

    # ElevenLabs only exposes calling through the batch-calls API, so a
    # single outbound attempt is submitted as a batch of one recipient.
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
                    dynamic_variables=build_outbound_dynamic_variables(attempt),
                ),
            )
        ],
        target_concurrency_limit=settings.elevenlabs_outbound_concurrency_limit,
    )

    return {
        "batch_call_id": response.id,
        "status": str(response.status),
        "total_calls_scheduled": response.total_calls_scheduled,
        "total_calls_dispatched": response.total_calls_dispatched,
    }
