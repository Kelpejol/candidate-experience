"""Core business logic for campaigns, campaign candidates, and outbound call attempts.

Owns the campaign lifecycle (draft -> uploaded -> survey_sent ->
outbound_ready -> outbound_calling), candidate roster management, applying
SurveyMonkey survey-status updates to candidates, and building/tracking the
queue of OutboundCallAttempt records used to dial non-responders. All
functions here perform direct DB reads/writes via SQLModel; none call
external services themselves.
"""

from sqlalchemy import or_, update
from sqlmodel import Session, select
from datetime import datetime
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.schemas.campaign import (
    CampaignCandidateCreate,
    CampaignCreate,
    CampaignCandidateSurveyStatusUpdate,
    CampaignStatusUpdate,
)
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services.outbound_retry_service import is_retryable_outbound_status


def parse_optional_datetime(value):
    """
    Parse an ISO-8601 datetime string into a datetime, passing through None
    or already-parsed datetime values unchanged.

    Converts a trailing "Z" (UTC designator) to "+00:00" since
    datetime.fromisoformat does not accept "Z" directly.
    """
    if value is None or isinstance(value, datetime):
        return value

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_latest_outbound_attempt_for_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    """Return the candidate's most recent OutboundCallAttempt (highest attempt_number), if any."""
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.candidate_id == candidate_id)
        .order_by(OutboundCallAttempt.attempt_number.desc())
    )

    return session.exec(statement).first()

def _person_match_filters(email: str | None, phone: str | None) -> list:
    """Filters identifying the same human across campaigns.

    Only non-empty values are matched — matching NULL to NULL would sweep in
    every row that happens to lack an email or phone.
    """
    filters = []
    if email:
        filters.append(CampaignCandidate.email == email)
    if phone:
        filters.append(CampaignCandidate.phone == phone)
    return filters


def suppress_person_from_calls(
    session: Session,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> int:
    """Mark this person opted out of calls in EVERY campaign they appear in.

    A candidate is one row per (campaign, person), so an opt-out recorded on a
    single row would only silence the current campaign — the same human would
    be called again by the next one. "Don't call me again" has to mean exactly
    that, so it is applied across all of their rows. Returns the row count.

    Does not commit — the caller owns the transaction.
    """
    filters = _person_match_filters(email, phone)
    if not filters:
        return 0

    rows = session.exec(
        select(CampaignCandidate).where(or_(*filters))
    ).all()
    for row in rows:
        row.opted_out_call = True
        session.add(row)
    return len(rows)


def is_person_opted_out_of_calls(
    session: Session,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> bool:
    """Whether this person has opted out of calls in any campaign.

    Consulted when candidates are added so a re-uploaded spreadsheet can't
    resurrect someone who already asked not to be called.
    """
    filters = _person_match_filters(email, phone)
    if not filters:
        return False

    return session.exec(
        select(CampaignCandidate.id)
        .where(or_(*filters))
        .where(CampaignCandidate.opted_out_call.is_(True))
        .limit(1)
    ).first() is not None


# How many eligible candidates are read per query when building a queue. The
# builders page through ALL of them — a campaign larger than one page must not
# be silently truncated, or the candidates past the cutoff are never called.
ELIGIBLE_PAGE_SIZE = 500


def iter_eligible_non_responders(campaign_id: str, session: Session):
    """Yield every outbound-eligible non-responder for a campaign, a page at a
    time.

    Offset paging is safe here because the eligibility filter (survey_status,
    opted_out_call, phone) is not modified while iterating — the queue builders
    only touch call_status — so the underlying result set is stable.
    """
    offset = 0
    while True:
        page = list_eligible_non_responders(
            campaign_id=campaign_id,
            session=session,
            limit=ELIGIBLE_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            return
        yield from page
        if len(page) < ELIGIBLE_PAGE_SIZE:
            return
        offset += ELIGIBLE_PAGE_SIZE


def build_outbound_retry_queue(
    campaign_id: str,
    session: Session,
    max_attempts: int = 3,
) -> list[OutboundCallAttempt]:
    """
    Create a new retry OutboundCallAttempt for each eligible non-responder
    whose latest attempt failed in a retryable way and hasn't hit
    `max_attempts` yet, and mark those candidates' call_status as "queued".

    Skips candidates with no prior attempt, at/above `max_attempts`, or
    whose latest attempt status is not retryable (see
    is_retryable_outbound_status). If any retry attempts are created, the
    campaign status is (re)set to "outbound_calling".

    Side effects: inserts new OutboundCallAttempt rows, updates candidate
    call_status and campaign status, and commits the session.
    """
    retry_attempts: list[OutboundCallAttempt] = []

    for candidate in iter_eligible_non_responders(campaign_id, session):
        latest_attempt = get_latest_outbound_attempt_for_candidate(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            session=session,
        )

        if not latest_attempt:
            continue

        if latest_attempt.attempt_number >= max_attempts:
            continue

        if not is_retryable_outbound_status(latest_attempt.status):
            continue

        retry_attempt = OutboundCallAttempt(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            candidate_name=candidate.candidate_name,
            candidate_email=candidate.email,
            campaign_name=candidate.campaign_name,
            tool_name=candidate.tool_name,
            phone=candidate.phone,
            attempt_number=latest_attempt.attempt_number + 1,
            status="queued",
        )

        retry_attempts.append(retry_attempt)
        session.add(retry_attempt)

        candidate.call_status = "queued"
        session.add(candidate)
    campaign = get_campaign_by_id(campaign_id, session)

    if campaign and retry_attempts:
        campaign.status = "outbound_calling"
        session.add(campaign)

    session.commit()

    for attempt in retry_attempts:
        session.refresh(attempt)

    return retry_attempts

def create_campaign(campaign_data: CampaignCreate, session: Session):
    """
    Create a new Campaign in "draft" status (the model's default) from the
    given data.

    Side effects: persists the new Campaign to the DB (add/commit/refresh).
    """
    campaign = Campaign(
        name=campaign_data.name,
        tool_name=campaign_data.tool_name,
        survey_id=campaign_data.survey_id,
        surveymonkey_collector_id=campaign_data.surveymonkey_collector_id,
        response_wait_hours=campaign_data.response_wait_hours,
        call_reason=campaign_data.call_reason,
        organization_name=campaign_data.organization_name,
        assessment_at=campaign_data.assessment_at,
        assessment_location=campaign_data.assessment_location,
        practice_test_url=campaign_data.practice_test_url,
        contact_info=campaign_data.contact_info,
    )

    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    return campaign



def get_campaign_by_id(campaign_id: str, session: Session) -> Campaign | None:
    """Fetch a Campaign by primary key, or None if it doesn't exist."""
    return session.get(Campaign, campaign_id)


def update_campaign_status(
    campaign: Campaign,
    status_update: CampaignStatusUpdate,
    session: Session,
) -> Campaign:
    """
    Update a campaign's status and stamp the corresponding timestamp the
    first time it enters certain milestone statuses: `survey_sent_at` on
    first transition to "survey_sent", `non_responder_checked_at` on first
    transition to "outbound_ready".

    Side effects: persists the change to the DB (add/commit/refresh).
    """
    campaign.status = status_update.status

    if status_update.status == "survey_sent" and campaign.survey_sent_at is None:
        campaign.survey_sent_at = datetime.utcnow()

    if status_update.status == "outbound_ready" and campaign.non_responder_checked_at is None:
        campaign.non_responder_checked_at = datetime.utcnow()

    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    return campaign


def list_campaigns(session: Session, limit: int = 50, offset: int = 0) -> list[Campaign]:
    """Return campaigns newest-first, paginated with limit/offset."""
    statement = (
        select(Campaign)
        .order_by(Campaign.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()





def add_campaign_candidates(
        campaign_id: str,
        candidates_data: list[CampaignCandidateCreate],
        session: Session
) -> list[CampaignCandidate]:
    """
    Bulk-create CampaignCandidate rows for a campaign from parsed upload
    data (see candidate_import_service.parse_candidate_upload).

    If the campaign exists, is currently "draft", and at least one
    candidate was added, advances the campaign status to "uploaded".

    Side effects: inserts CampaignCandidate rows, may update campaign
    status, and commits the session.
    """
    candidates = []

    for candidate_data in candidates_data:
        email = str(candidate_data.email) if candidate_data.email else None

        # A previous "don't call me again" outranks whatever the upload says —
        # otherwise re-uploading a spreadsheet next month would resurrect
        # someone who already opted out.
        opted_out_call = candidate_data.opted_out_call or is_person_opted_out_of_calls(
            session, email=email, phone=candidate_data.phone
        )

        candidate = CampaignCandidate(
            campaign_id=campaign_id,
            candidate_name=candidate_data.candidate_name,
            email=email,
            phone=candidate_data.phone,
            tool_name=candidate_data.tool_name,
            campaign_name=candidate_data.campaign_name,
            external_candidate_id=candidate_data.external_candidate_id,
            opted_out_call=opted_out_call,
            opted_out_email=candidate_data.opted_out_email,
        )

        candidates.append(candidate)

    session.add_all(candidates)

    campaign = get_campaign_by_id(campaign_id, session)

    if campaign and candidates and campaign.status == "draft":
        campaign.status = "uploaded"
        session.add(campaign)
    session.commit()


    for candidate in candidates:
        session.refresh(candidate)


    return candidates
    


def list_campaign_candidates(
        campaign_id: str,
        session: Session,
        limit: int = 100,
        offset: int = 0,
) -> list[CampaignCandidate]:
    """Return a campaign's candidates, oldest-first, paginated with limit/offset."""
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .order_by(CampaignCandidate.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    
    return session.exec(statement).all()


def count_by(values: list[str]) -> dict[str, int]:
    """Return a mapping of each distinct value to its occurrence count in `values`."""
    counts: dict[str, int] = {}

    for value in values:
        counts[value] = counts.get(value, 0) + 1

    return counts


def get_campaign_summary(
    campaign_id: str,
    session: Session,
) -> dict:
    """
    Build a summary dict for a campaign: total candidate count, and
    breakdowns of candidate survey_status, candidate call_status, and
    outbound attempt status, each as a {value: count} mapping.

    Loads up to 500 candidates/attempts per campaign for the counts (read-only).
    """
    candidates = list_campaign_candidates(
        campaign_id=campaign_id,
        session=session,
        limit=500,
        offset=0,
    )

    attempts = list_outbound_call_attempts(
        campaign_id=campaign_id,
        session=session,
        limit=500,
        offset=0,
    )

    return {
        "campaign_id": campaign_id,
        "total_candidates": len(candidates),
        "survey_statuses": count_by([candidate.survey_status for candidate in candidates]),
        "call_statuses": count_by([candidate.call_status for candidate in candidates]),
        "outbound_attempts": count_by([attempt.status for attempt in attempts]),
    }


def get_campaign_candidate_by_id(
        campaign_id: str,
        candidate_id: str,
        session: Session,
) -> CampaignCandidate | None:
    """Fetch a single candidate scoped to a campaign, or None if not found."""
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .where(CampaignCandidate.id == candidate_id)
    )

    return session.exec(statement).first()



def update_campaign_candidate_survey_status(
    candidate: CampaignCandidate,
    status_update: CampaignCandidateSurveyStatusUpdate,
    session: Session
) -> CampaignCandidate:
    """
    Set a candidate's survey_status directly to the given value.

    Side effects: persists the change to the DB (add/commit/refresh).
    """
    candidate.survey_status = status_update.survey_status

    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    return candidate



def list_eligible_non_responders(
        campaign_id: str,
        session: Session,
        limit: int = 100,
        offset: int = 0
) -> list[CampaignCandidate]:
    """
    Return candidates in a campaign who are outbound-call eligible: their
    survey_status is "non_responder", they have not opted out of calls, and
    they have a non-empty phone number. Ordered oldest-first, paginated.
    """
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .where(CampaignCandidate.survey_status == "non_responder")
        .where(CampaignCandidate.opted_out_call.is_(False))
        .where(CampaignCandidate.phone.is_not(None))
        .where(CampaignCandidate.phone != "")
        .order_by(CampaignCandidate.created_at.asc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()





def get_existing_outbound_attempt_for_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    """Return any existing OutboundCallAttempt for a candidate in a campaign (any status), if one exists."""
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.candidate_id == candidate_id)
    )

    return session.exec(statement).first()


def build_outbound_call_queue(
    campaign_id: str,
    session: Session,
) -> list[OutboundCallAttempt]:
    """
    Build the initial outbound-call queue for a campaign: for each eligible
    non-responder without an existing attempt, create a first
    OutboundCallAttempt (attempt_number=1, status="queued") and mark the
    candidate's call_status as "queued". Candidates that already have an
    attempt (e.g. from a re-run) have that attempt reused/returned as-is
    rather than duplicated.

    If any attempts (new or existing) are returned, the campaign status is
    set to "outbound_calling".

    Side effects: inserts new OutboundCallAttempt rows, updates candidate
    call_status and campaign status, and commits the session.
    """
    attempts: list[OutboundCallAttempt] = []

    for candidate in iter_eligible_non_responders(campaign_id, session):
        existing_attempt = get_existing_outbound_attempt_for_candidate(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            session=session,
        )

        if existing_attempt:
            attempts.append(existing_attempt)
            continue

        attempt = OutboundCallAttempt(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            candidate_name=candidate.candidate_name,
            candidate_email=candidate.email,
            campaign_name=candidate.campaign_name,
            tool_name=candidate.tool_name,
            phone=candidate.phone,
            attempt_number=1,
            status="queued",
        )
        attempts.append(attempt)
        session.add(attempt)

        candidate.call_status = "queued"
        session.add(candidate)
    campaign = get_campaign_by_id(campaign_id, session)

    if campaign and attempts:
        campaign.status = "outbound_calling"
        session.add(campaign)

    session.commit()

    for attempt in attempts:
        session.refresh(attempt)

    return attempts


def list_outbound_call_attempts(
    campaign_id: str,
    session: Session,
    limit: int = 100,
    offset: int = 0,
) -> list[OutboundCallAttempt]:
    """Return a campaign's outbound call attempts, oldest-first, paginated with limit/offset."""
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .order_by(OutboundCallAttempt.created_at.asc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()


def get_outbound_call_attempt_by_id(
    campaign_id: str,
    attempt_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    """Fetch a single outbound call attempt scoped to a campaign, or None if not found."""
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.id == attempt_id)
    )

    return session.exec(statement).first()


def update_outbound_call_attempt_status(
    attempt: OutboundCallAttempt,
    status_update,
    session: Session,
) -> OutboundCallAttempt:
    """
    Apply a status update to an outbound call attempt (status, disposition,
    ElevenLabs conversation id, transcript, summary, recording URL), and
    stamp `started_at`/`ended_at` based on the new status.

    Sets `started_at` the first time the status becomes "calling". Sets
    `ended_at` whenever the status is one of the call-terminal statuses
    (answered, responded_by_call, no_answer, busy, voicemail, failed,
    opted_out, handed_off_to_human).

    Side effects: persists the change to the DB (add/commit/refresh).
    """
    attempt.status = status_update.status
    attempt.disposition = status_update.disposition
    attempt.elevenlabs_conversation_id = status_update.elevenlabs_conversation_id
    attempt.transcript = status_update.transcript
    attempt.summary = status_update.summary
    attempt.recording_url = status_update.recording_url

    if status_update.status == "calling" and attempt.started_at is None:
        attempt.started_at = datetime.utcnow()

    if status_update.status in {
        "answered",
        "responded_by_call",
        "no_answer",
        "busy",
        "voicemail",
        "failed",
        "opted_out",
        "handed_off_to_human",
    }:
        attempt.ended_at = datetime.utcnow()

    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    return attempt



def apply_candidate_survey_updates(
        campaign_id: str,
        updates: list[dict],
        session: Session
) -> list[CampaignCandidate]:
    """
    Apply a batch of SurveyMonkey response-sync updates to candidates in a
    campaign (e.g. from surveymonkey_sync_service).

    Each `update` dict is expected to have keys: candidate_id,
    surveymonkey_recipient_id, surveymonkey_response_id,
    surveymonkey_response_status, survey_status, survey_responded_at (ISO
    string or None). Updates referencing a candidate_id not found in this
    campaign are silently skipped.

    If any candidates were updated, advances the campaign status to
    "outbound_ready" and, if not already set, stamps
    non_responder_checked_at.

    Side effects: persists candidate and campaign changes to the DB
    (add/commit/refresh).
    """
    updated_candidates: list[CampaignCandidate] = []

    for update in updates:
        candidate = get_campaign_candidate_by_id(
            campaign_id=campaign_id,
            candidate_id=update["candidate_id"],
            session=session
        )


        if not candidate:
            continue

        candidate.surveymonkey_recipient_id = update["surveymonkey_recipient_id"]
        candidate.surveymonkey_response_id = update["surveymonkey_response_id"]
        candidate.surveymonkey_response_status = update["surveymonkey_response_status"]
        candidate.survey_status = update["survey_status"]
        candidate.survey_responded_at = parse_optional_datetime(update["survey_responded_at"])
        session.add(candidate)
        updated_candidates.append(candidate)
    campaign = get_campaign_by_id(campaign_id, session)

    if campaign and updated_candidates:
        campaign.status = "outbound_ready"

        if campaign.non_responder_checked_at is None:
            campaign.non_responder_checked_at = datetime.utcnow()

        session.add(campaign)

    session.commit()

    for candidate in updated_candidates:
        session.refresh(candidate)

    
    return updated_candidates


def get_next_queued_outbound_attempt(
    campaign_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    """Return the oldest still-"queued" outbound call attempt for a campaign, if any (FIFO, read-only)."""
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.status == "queued")
        .order_by(OutboundCallAttempt.created_at.asc())
    )

    return session.exec(statement).first()


def claim_next_queued_outbound_attempt(
    campaign_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    """
    Atomically-in-intent "claim" the next queued outbound call attempt for
    a campaign: transition it and its candidate to "calling" status so a
    worker can place the call.

    Returns None if there is no queued attempt. Sets the attempt's
    started_at timestamp when claimed.

    Concurrency-safe: the claim is a conditional UPDATE that only succeeds if
    the row is still "queued", so if two workers race for the same attempt
    exactly one wins (rowcount==1) and the loser moves on to the next queued
    attempt — a candidate is never dialed twice. The attempt and its
    candidate are flipped to "calling" in one transaction.

    Side effects: persists the attempt and (if found) candidate status
    changes to the DB.
    """
    while True:
        attempt = get_next_queued_outbound_attempt(
            campaign_id=campaign_id,
            session=session,
        )

        if not attempt:
            return None

        attempt_id = attempt.id
        candidate_id = attempt.candidate_id

        result = session.execute(
            update(OutboundCallAttempt)
            .where(OutboundCallAttempt.id == attempt_id)
            .where(OutboundCallAttempt.status == "queued")
            .values(status="calling", started_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            # Another worker claimed this attempt first. Discard our stale view
            # and try the next queued one.
            session.rollback()
            session.expire_all()
            continue

        candidate = get_campaign_candidate_by_id(
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            session=session,
        )

        if candidate:
            candidate.call_status = "calling"
            session.add(candidate)

        session.commit()

        # The Core UPDATE bypassed the ORM object, so reload it before returning.
        session.expire(attempt)
        session.refresh(attempt)

        return attempt
