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

    # --- Inbound IVR / KB scoping (calling-agent campaign IVR) ---------------

    # Whether this campaign is currently available to inbound callers. The AI
    # only scopes to and answers for campaigns that are active right now.
    inbound_active: bool = Field(default=True)
    # Optional active window — if set, the campaign counts as inbound-active
    # only between these times, so staff can switch it on/off for a duration.
    active_from: datetime | None = Field(default=None)
    active_until: datetime | None = Field(default=None)

    # Where this campaign's knowledge base content comes from: a SharePoint link
    # (later) or a local path (during development). The ingestion step reads
    # this, chunks + embeds it, and tags the chunks with this campaign.
    kb_source: str | None = Field(default=None)
    # The tag retrieval filters on to return ONLY this campaign's KB slice.
    # Defaults to the campaign's own id at ingestion time if left unset.
    kb_scope: str | None = Field(default=None)

    # --- Outbound call context (fills the outreach script's [brackets]) ------

    # What the outbound agent says it's calling about; selects which version of
    # the core message it uses. One of OUTBOUND_CALL_REASONS.
    call_reason: str | None = Field(default=None)
    # Who the agent says it's calling on behalf of. Falls back to `name`.
    organization_name: str | None = Field(default=None)
    # The candidate's assessment date & time (campaign-wide).
    assessment_at: datetime | None = Field(default=None)
    # Test centre address or the online test link, spoken as-is.
    assessment_location: str | None = Field(default=None)
    # Link to the practice test / system checks, offered when not yet done.
    practice_test_url: str | None = Field(default=None)
    # Phone/email the agent gives for callbacks and voicemail.
    contact_info: str | None = Field(default=None)