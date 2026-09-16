"""One survey answer a candidate gave during an outbound survey call.

The outbound agent asks the campaign's survey questions by phone and records
each answer here as the caller gives it, tagged to the OutboundCallAttempt it
came from. This is our durable record of the responses collected by voice —
independent of the post-call webhook, which may arrive late or not at all.
"""

from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class OutboundSurveyAnswer(SQLModel, table=True):
    __tablename__ = "outbound_survey_answer"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    outbound_attempt_id: str = Field(index=True)
    campaign_id: str = Field(index=True)
    candidate_id: str = Field(index=True)
    # 1-based position of the question in the survey (as served to the agent).
    position: int | None = Field(default=None)
    question: str
    # How the question was answered — "choice" / "multi" / "text" (advisory).
    answer_type: str | None = Field(default=None)
    answer: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
