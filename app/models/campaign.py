"""SQLModel table for recruiting campaigns: the top-level grouping of
candidates that are sent a survey and, for non-responders, an outbound call.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel
from uuid import uuid4


class Campaign(SQLModel, table=True):
    """A recruiting/candidate-experience campaign.

    One row per campaign (e.g. a CSAT or candidate feedback push tied to a
    specific tool/survey rollout). Owns a batch of CampaignCandidate rows and
    is linked to a SurveyMonkey survey/collector used to distribute and
    collect responses.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    name: str = Field(index=True)
    tool_name: str | None = Field(default=None, index=True)

    # SurveyMonkey survey and collector (distribution channel) used to send
    # and track this campaign's survey.
    survey_id: str | None = Field(default=None)
    surveymonkey_collector_id: str | None = Field(default=None)

    # Lifecycle state, e.g. "draft" while candidates are being set up before
    # the survey is sent.
    status: str = Field(default="draft", index=True)
    # Hours to wait after sending the survey before a candidate who hasn't
    # responded is treated as a non-responder eligible for an outbound call.
    response_wait_hours: int = Field(default=24)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    survey_sent_at: datetime | None = Field(default=None)
    non_responder_checked_at: datetime | None = Field(default=None)