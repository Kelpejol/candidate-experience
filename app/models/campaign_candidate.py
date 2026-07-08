"""SQLModel table linking a Campaign to the individual candidates enrolled
in it, tracking each candidate's survey and outbound-call progress.
"""

from datetime import datetime
from uuid import uuid4

from sqlmodel import SQLModel, Field


class CampaignCandidate(SQLModel, table=True):
    """A single candidate's membership and progress within a Campaign.

    One row per (campaign, candidate) pair, imported from an external ATS
    or upload. Tracks the SurveyMonkey survey/response linkage, whether the
    candidate has been (or should be) called, and opt-out preferences.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    campaign_id: str =Field(index=True)

    candidate_name: str | None = Field(default=None)
    email: str | None = Field(default=None, index=True)
    phone: str | None = Field(default=None, index=True)

    tool_name: str | None = Field(default=None, index=True)
    campaign_name: str | None = Field(default=None)
    # Candidate id from the source system (e.g. ATS) the campaign was
    # imported from.
    external_candidate_id: str | None = Field(default=None, index=True)

    # Identifiers linking this candidate to their SurveyMonkey collector
    # recipient and, once they respond, their survey response.
    surveymonkey_recipient_id: str | None = Field(default=None, index=True)
    surveymonkey_response_id: str | None = Field(default=None, index=True)
    surveymonkey_response_status: str | None = Field(default=None)
    survey_responded_at: datetime | None = Field(default=None)

    # e.g. "not_sent" / "sent" / "responded" / "non_responder".
    survey_status: str = Field(default="not_sent", index=True)
    # e.g. "not_queued" / "queued" / "calling" / "completed" / "failed" /
    # "opted_out" — tracks the candidate's outbound call pipeline state.
    call_status: str = Field(default="not_queued", index=True)

    opted_out_call: bool = Field(default=False)
    opted_out_email: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)