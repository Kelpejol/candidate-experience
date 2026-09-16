"""SQLModel table for post-call CSAT invitations.

Represents a satisfaction survey sent to a candidate after an inbound support
call (PSA Flow E). One row per source call. Tracks the SurveyMonkey linkage
(survey/collector/message/recipient/response) and the invitation lifecycle so
the send and response-sync jobs can drive it. Mirrors the CSATInvitation model
in docs/campaign-survey-outbound-csat-plan.md.
"""

from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class CsatInvitation(SQLModel, table=True):
    """A single post-call CSAT invitation and its outcome."""

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    # The call this CSAT follows. Unique so we never send two CSATs for one call.
    call_record_id: str = Field(index=True, unique=True)

    candidate_email: str | None = Field(default=None, index=True)
    candidate_phone: str | None = Field(default=None)

    # SurveyMonkey linkage. survey_id/collector_id come from config (the standing
    # CSAT survey); message/recipient/response ids are filled as the invitation
    # is sent and later answered.
    surveymonkey_survey_id: str | None = Field(default=None)
    surveymonkey_collector_id: str | None = Field(default=None)
    surveymonkey_message_id: str | None = Field(default=None)
    surveymonkey_recipient_id: str | None = Field(default=None, index=True)
    surveymonkey_response_id: str | None = Field(default=None)

    # Lifecycle: pending_contact_lookup | ready_to_send | sending | sent
    #          | responded | skipped_opt_out | failed
    # "sending" is claimed (and committed) BEFORE the real SurveyMonkey send,
    # so a crash mid-send leaves the invitation there rather than back at
    # ready_to_send, where the next batch run would email the person twice.
    status: str = Field(default="ready_to_send", index=True)

    # Populated from the response later (optional; extraction is a refinement).
    score: int | None = Field(default=None)
    comment: str | None = Field(default=None)

    sent_at: datetime | None = Field(default=None)
    responded_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
