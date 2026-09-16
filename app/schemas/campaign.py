"""Pydantic schemas for campaigns, campaign candidates, and outbound call attempts.

Covers the request/response shapes used by the campaigns API: creating and
reading campaigns, uploading/reading candidates, updating survey/call
status, and reading outbound call attempts and survey sync summaries.
"""

from pydantic import BaseModel, Field, field_validator, EmailStr

from datetime import datetime

from typing import Literal

from app.core.vocabulary import is_tool_allowed

CallReason = Literal["schedule_confirmation", "reminder", "no_show_followup"]


def _validate_local_wall_clock(v: datetime | None):
    """Reject a timezone-aware assessment time.

    `assessment_at` is local wall-clock: the agent reads it back to candidates
    exactly as entered. The DB column drops tzinfo on write, so an aware value
    (e.g. "…T09:00:00Z" from a script or integration) would be silently stored
    as 09:00 and spoken as the wrong local time. Better to reject it than to
    tell candidates the wrong hour.
    """
    if v is not None and v.tzinfo is not None:
        raise ValueError(
            "assessment_at must be a local wall-clock time without a timezone "
            "offset (e.g. 2026-09-02T10:00:00) — it is read back to candidates "
            "exactly as entered"
        )
    return v


# Campaign lifecycle: draft -> uploaded -> survey_sending/sent -> waiting_for_responses
# -> non_response_checking -> outbound_ready -> outbound_calling -> completed/failed.
CampaignStatus = Literal[
   "draft",
    "uploaded",
    "survey_sending",
    "survey_sent",
    "waiting_for_responses",
    "non_response_checking",
    "outbound_ready",
    "outbound_calling",
    "completed",
    "failed",
]

SurveyStatus = Literal[
    "not_sent",
    "sent",
    "responded",
    "partial_response",
    "non_responder",
    "excluded_opt_out",
    "failed"
]

CallStatus = Literal[
    "not_queued",
    "queued",
    "calling",
    "answered",
    "responded_by_call",
    "no_answer",
    "busy",
    "voicemail",
    "failed",
    "opted_out",
    "handed_off_to_human",
]



class CampaignCreate(BaseModel):
    """Request shape for creating a new campaign.

    `response_wait_hours` bounds how long the system waits for survey
    responses before treating a candidate as a non-responder (1 to 336
    hours, default 24). `tool_name`, when provided, must be a recognized
    tool per app.core.vocabulary.is_tool_allowed.
    """

    name: str = Field(min_length=1)
    tool_name: str | None = None
    survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    response_wait_hours: int = Field(default=24, ge=1, le=336)

    # Outbound call context (fills the outreach script's [brackets]).
    call_reason: CallReason | None = None
    organization_name: str | None = None
    assessment_at: datetime | None = None
    assessment_location: str | None = None
    practice_test_url: str | None = None
    contact_info: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str):
        """Reject a name that is blank or whitespace-only."""
        if not v.strip():
            raise ValueError("Campaign name must not be empty")
        return v

    @field_validator("tool_name")
    @classmethod
    def tool_name_must_be_allowed(cls, v: str | None):
        """Reject tool_name values outside app.core.vocabulary.ALLOWED_TOOLS. None is allowed (field is optional)."""
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v

    _assessment_at_is_local = field_validator("assessment_at")(
        _validate_local_wall_clock
    )


class CampaignRead(BaseModel):
    """Response shape for a persisted campaign, including lifecycle status and timestamps."""

    id: str
    name: str
    tool_name: str | None = None
    survey_id: str | None = None
    surveymonkey_collector_id: str | None = None
    status: CampaignStatus
    response_wait_hours: int
    created_at: datetime
    survey_sent_at: datetime | None = None
    non_responder_checked_at: datetime | None = None

    # Inbound IVR / KB scoping
    inbound_active: bool = True
    active_from: datetime | None = None
    active_until: datetime | None = None
    kb_source: str | None = None
    kb_scope: str | None = None

    # Outbound call context
    call_reason: CallReason | None = None
    organization_name: str | None = None
    assessment_at: datetime | None = None
    assessment_location: str | None = None
    practice_test_url: str | None = None
    contact_info: str | None = None


class CampaignOutboundUpdate(BaseModel):
    """Partial update of a campaign's outbound call context. Only the fields
    provided are changed (model_dump(exclude_unset=True))."""

    call_reason: CallReason | None = None
    organization_name: str | None = None
    assessment_at: datetime | None = None
    assessment_location: str | None = None
    practice_test_url: str | None = None
    contact_info: str | None = None

    _assessment_at_is_local = field_validator("assessment_at")(
        _validate_local_wall_clock
    )


class CampaignStatusUpdate(BaseModel):
    """Request shape for transitioning a campaign to a new CampaignStatus."""

    status: CampaignStatus


class CampaignSurveyTemplateCreate(BaseModel):
    """Request shape for creating a campaign survey from a selected SurveyMonkey template.

    If `template_survey_id` is omitted, the backend uses the default
    SURVEYMONKEY_CAMPAIGN_TEMPLATE_SURVEY_ID setting.
    """

    template_survey_id: str | None = None


class CampaignSurveyRecipientsPrepare(BaseModel):
    """Request shape for adding campaign candidates to a SurveyMonkey message.

    This prepares recipients only. It does not send the email invitation.
    """

    collector_id: str | None = None
    message_id: str


class CampaignSurveyRecipientsPrepareResponse(BaseModel):
    """Summary returned after candidate emails are added to a SurveyMonkey message."""

    campaign_id: str
    collector_id: str
    message_id: str
    eligible_candidates: int
    skipped_candidates: int
    prepared_recipients: int


class CampaignSurveyMessageCreate(BaseModel):
    """Request shape for creating a SurveyMonkey collector message draft.

    This creates an unsent message only. Recipients and sending are separate
    explicit steps.
    """

    collector_id: str | None = None
    collector_name: str | None = None
    subject: str = Field(min_length=1)
    body: str | None = None

    @field_validator("body")
    @classmethod
    def body_must_include_surveymonkey_link(cls, v: str | None):
        """SurveyMonkey requires invite bodies to include survey and opt-out placeholders."""
        if v is None:
            return v

        survey_tokens = (
            "[SurveyLink]",
            "{{SurveyLink}}",
            "[FirstQuestion]",
            "{{FirstQuestion}}",
        )

        opt_out_tokens = (
            "[OptOutLink]",
            "{{OptOutLink}}",
        )
        footer_tokens = (
            "[FooterLink]",
            "{{FooterLink}}",
        )
        privacy_tokens = (
            "[PrivacyLink]",
            "{{PrivacyLink}}",
        )

        if not any(token in v for token in survey_tokens):
            raise ValueError("Message body must include [SurveyLink] or [FirstQuestion]")

        if not any(token in v for token in opt_out_tokens):
            raise ValueError("Message body must include [OptOutLink]")

        if not any(token in v for token in footer_tokens):
            raise ValueError("Message body must include [FooterLink]")

        if not any(token in v for token in privacy_tokens):
            raise ValueError("Message body must include [PrivacyLink]")

        return v


class CampaignSurveyMessageCreateResponse(BaseModel):
    """Response after creating a SurveyMonkey collector/message draft."""

    campaign_id: str
    survey_id: str
    collector_id: str
    message_id: str
    subject: str


class CampaignSurveyMessageSend(BaseModel):
    """Request shape for sending a prepared SurveyMonkey message.

    Sending is irreversible, so callers must pass the exact confirmation
    phrase in `confirm_send`.
    """

    collector_id: str | None = None
    message_id: str
    confirm_send: str


class CampaignSurveyMessageSendResponse(BaseModel):
    """Response after SurveyMonkey accepts a message send request."""

    campaign_id: str
    collector_id: str
    message_id: str
    sent: bool
    updated_candidates: int


class SurveyMonkeyTemplateRead(BaseModel):
    """Response shape for a SurveyMonkey survey that can be used as a template."""

    id: str
    title: str
    nickname: str | None = None


class CampaignSummaryRead(BaseModel):
    """Response shape aggregating a campaign's candidate counts by survey status, call status, and outbound attempt outcome."""

    campaign_id: str
    total_candidates: int
    survey_statuses: dict[str, int]
    call_statuses: dict[str, int]
    outbound_attempts: dict[str, int]


class CampaignCandidateCreate(BaseModel):
    """Request shape for adding/uploading a candidate to a campaign.

    All fields besides the opt-out flags are optional to accommodate
    partial candidate data from uploads. `tool_name`, when provided, must
    be a recognized tool per app.core.vocabulary.is_tool_allowed.
    """

    candidate_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    tool_name: str | None = None
    campaign_name: str | None = None
    external_candidate_id: str | None = None
    opted_out_call: bool = False
    opted_out_email: bool = False


    @field_validator("tool_name")
    @classmethod
    def candidate_tool_name_must_be_allowed(cls, v: str | None):
        """Reject tool_name values outside app.core.vocabulary.ALLOWED_TOOLS. None is allowed (field is optional)."""
        if v is not None and not is_tool_allowed(v):
            raise ValueError("Unknown tool name")
        return v
    
    @field_validator("phone")
    @classmethod
    def phone_must_not_be_empty(cls, v: str | None):
        """Reject a phone value that is present but blank/whitespace-only. None is allowed (field is optional)."""
        if v is not None and not v.strip():
            raise ValueError("Phone must not be empty")
        return v
    


class CampaignCandidateRead(BaseModel):
    """Response shape for a persisted campaign candidate, including survey and call progress."""

    id: str
    campaign_id: str
    candidate_name: str | None = None
    email: str | None = None
    phone: str | None = None
    tool_name: str | None = None
    campaign_name: str | None = None
    external_candidate_id: str | None = None
    surveymonkey_recipient_id: str | None = None
    surveymonkey_response_id: str | None = None
    surveymonkey_response_status: str | None = None
    survey_responded_at: datetime | None = None
    survey_status: SurveyStatus
    call_status: CallStatus
    opted_out_call: bool
    opted_out_email: bool
    created_at: datetime
    updated_at: datetime




class CampaignCandidateSurveyStatusUpdate(BaseModel):
    """Request shape for setting a candidate's survey status directly."""

    survey_status: SurveyStatus




OutboundCallAttemptStatus = Literal[
    "queued",
    "calling",
    "answered",
    "responded_by_call",
    "no_answer",
    "busy",
    "voicemail",
    "failed",
    "opted_out",
    "handed_off_to_human",
]


class OutboundCallAttemptRead(BaseModel):
    """Response shape for a single outbound call attempt against a candidate, including transcript/recording once available."""

    id: str
    campaign_id: str
    candidate_id: str
    candidate_name: str | None = None
    candidate_email: str | None = None
    campaign_name: str | None = None
    tool_name: str | None = None
    phone: str
    attempt_number: int
    status: OutboundCallAttemptStatus
    disposition: str | None = None
    elevenlabs_conversation_id: str | None = None
    transcript: str | None = None
    summary: str | None = None
    recording_url: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime

class OutboundCallAttemptStatusUpdate(BaseModel):
    """Request shape for updating an outbound call attempt's status and outcome details, typically from a provider callback."""

    status: OutboundCallAttemptStatus
    disposition: str | None = None
    elevenlabs_conversation_id: str | None = None
    transcript: str | None = None
    summary: str | None = None
    recording_url: str | None = None

class CampaignSurveySyncResponse(BaseModel):
    """Response shape summarizing the outcome of a SurveyMonkey response sync for a campaign."""

    total_recipients: int
    completed: int
    partial: int
    non_responders: int
    updated_candidates: int
