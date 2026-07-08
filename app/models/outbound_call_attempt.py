"""SQLModel table for outbound call attempts: individual retries of the
ElevenLabs outbound call placed to a non-responding campaign candidate.
"""

from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class OutboundCallAttempt(SQLModel, table=True):
    """A single attempt to reach a candidate by outbound voice call.

    One row per attempt (a candidate may have several, per
    `attempt_number`, if earlier attempts fail and are retried). Denormalizes
    candidate/campaign details so an attempt row stays meaningful even if
    the source campaign/candidate changes, and records the resulting
    ElevenLabs conversation id, transcript, and disposition once the call
    completes.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    campaign_id: str = Field(index=True)
    candidate_id: str = Field(index=True)

    candidate_name: str | None = Field(default=None)
    candidate_email: str | None = Field(default=None, index=True)
    campaign_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)

    phone: str = Field(index=True)
    # 1-based count of how many times this candidate has been called for
    # this campaign; used to drive retry limits/backoff.
    attempt_number: int = Field(default=1)

    # e.g. "queued" / "calling" / "completed" / "failed".
    status: str = Field(default="queued", index=True)
    disposition: str | None = Field(default=None)

    # Conversation id returned by ElevenLabs for the placed call; used to
    # correlate this attempt with call status/transcript webhooks.
    elevenlabs_conversation_id: str | None = Field(default=None, index=True)
    transcript: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)

    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)