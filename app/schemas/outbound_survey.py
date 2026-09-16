"""Request/response shapes for the outbound survey voice-agent tools."""

from datetime import datetime

from pydantic import BaseModel, Field


class OutboundQuestionsRequest(BaseModel):
    """The agent sends the campaign it's calling for (from its dynamic
    variables) to fetch the questions to ask."""

    campaign_id: str = Field(min_length=1)


class SpokenQuestion(BaseModel):
    position: int
    text: str
    # "choice" (pick one), "multi" (pick any), or "text" (free-spoken answer).
    type: str
    options: list[str] = []


class OutboundQuestionsResponse(BaseModel):
    campaign_id: str
    questions: list[SpokenQuestion]


class OutboundAnswerRequest(BaseModel):
    """The agent records one answer as the caller gives it. It identifies the
    call by the attempt + campaign it was launched with; the candidate is
    derived from the attempt (never trusted from the agent)."""

    outbound_attempt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    position: int | None = None
    type: str | None = None


class OutboundAnswerResponse(BaseModel):
    saved: bool
    answer_id: str


class OutboundOptOutRequest(BaseModel):
    """The caller asked not to be contacted again."""

    outbound_attempt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)


class OutboundOptOutResponse(BaseModel):
    saved: bool


class OutboundSurveyAnswerRead(BaseModel):
    id: str
    outbound_attempt_id: str
    candidate_id: str
    candidate_name: str | None = None
    position: int | None = None
    question: str
    answer_type: str | None = None
    answer: str
    created_at: datetime
