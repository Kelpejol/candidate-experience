"""SQLModel table for call records: the log of voice calls (inbound or
outbound) handled by the voice agent, including any human handoff outcome.
"""

from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class CallRecord(SQLModel, table=True):
    """A single voice call between the AI agent and a candidate.

    One row per call (inbound support call or outbound campaign call).
    Tracks the call's disposition and transcript, plus, if the call was
    escalated to a human, the outcome of that handoff leg.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    # Call id assigned by the external voice provider (e.g. ElevenLabs/Twilio
    # conversation/call sid); unique so a webhook can upsert idempotently.
    external_call_id: str = Field(index=True, unique=True)
    direction: str
    candidate_phone: str | None = Field(default=None)
    candidate_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    campaign_name: str | None = Field(default=None)
    disposition: str
    issue_summary: str | None = Field(default=None)
    transcription: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)
    # Fields below describe the outcome of a warm handoff to a human agent,
    # when the call was escalated via a TwiML <Dial> leg.
    handoff_call_sid: str | None = Field(default=None)
    handoff_status: str | None = Field(default=None)
    handoff_duration: int | None = Field(default=None)
    handoff_bridged: bool | None = Field(default=None)
    call_start_time: datetime | None = Field(default=None)
    call_end_time: datetime | None = Field(default=None)


    created_at: datetime = Field(default_factory=datetime.utcnow)