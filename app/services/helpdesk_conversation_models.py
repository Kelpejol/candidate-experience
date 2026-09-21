"""Validated model boundaries for conversation reasoning and answer review."""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.services.helpdesk_classifier import TicketClassification


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observation(StrictModel):
    kind: Literal["goal", "stage", "error", "device", "attempt", "outcome", "other"]
    value: str = Field(min_length=1, max_length=600)
    source_id: str
    quote: str = Field(min_length=1, max_length=1000)


class Understanding(StrictModel):
    classification: TicketClassification
    issue: str = Field(min_length=1, max_length=1200)
    observations: list[Observation] = Field(default_factory=list, max_length=30)
    missing: list[str] = Field(default_factory=list, max_length=10)
    needs_tool: bool
    next_question: str = Field(default="", max_length=800)
    question_target: str = Field(default="", max_length=120)
    question_strategy: Literal["direct", "options", "screenshot", "locate_information"] = "direct"
    question_purpose: str = Field(default="", max_length=500)
    useful_next_step: bool
    escalate: bool = False
    reason: str = Field(max_length=1200)


class AnswerReview(StrictModel):
    supported: bool
    addresses_request: bool
    applicable: bool
    repeats_failed_fix: bool
    reason: str = Field(max_length=1200)


class ConversationState(TypedDict, total=False):
    event_id: str
    latest_id: str
    latest_text: str
    subject: str
    messages: list[dict]
    attachment_notes: list[str]
    sentiment: str | None
    channel: str
    candidate_name: str | None
    registry: dict
    resolution: dict
    understanding: dict
    observations: list[dict]
    questions: list[dict]
    previous_issue: str
    decision: dict
    chunks: list[dict]
    reply: str | None
    grounding_status: str | None
    failure: str | None
