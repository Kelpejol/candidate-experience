"""API routes for managing recruiting campaigns.

Covers campaign CRUD and lifecycle status, adding/uploading candidates,
tracking candidate survey status and non-responders, triggering
SurveyMonkey response syncs (synchronous or via background job), and
building/managing the outbound call queue (including retries and
per-attempt status updates), plus enqueueing async jobs backed by the
Redis job queue.
"""

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File

from app.services.candidate_import_service import parse_candidate_upload

from app.schemas.campaign import CampaignRead, CampaignCreate, CampaignCandidateRead, CampaignCandidateCreate, CampaignCandidateSurveyStatusUpdate, OutboundCallAttemptRead, CampaignSurveySyncResponse, OutboundCallAttemptStatusUpdate, CampaignStatusUpdate, CampaignSummaryRead, CampaignSurveyTemplateCreate, SurveyMonkeyTemplateRead, CampaignSurveyRecipientsPrepare, CampaignSurveyRecipientsPrepareResponse, CampaignSurveyMessageCreate, CampaignSurveyMessageCreateResponse, CampaignSurveyMessageSend, CampaignSurveyMessageSendResponse
from sqlmodel import Session
from app.services.campaign_service import (
    create_campaign as create_campaign_service,
    list_campaigns as list_campaigns_service,
    get_campaign_by_id,
    add_campaign_candidates,
    list_campaign_candidates as list_campaign_candidates_service,
    get_campaign_candidate_by_id,
    update_campaign_candidate_survey_status,
    list_eligible_non_responders as list_eligible_non_responders_service,
    build_outbound_call_queue as build_outbound_call_queue_service,
   list_outbound_call_attempts as list_outbound_call_attempts_service,
   get_outbound_call_attempt_by_id,
   update_outbound_call_attempt_status,
   build_outbound_retry_queue as build_outbound_retry_queue_service,
   get_next_queued_outbound_attempt,
   update_campaign_status,
   get_campaign_summary as get_campaign_summary_service,
    )
from app.core.queue import get_default_queue
from app.core.redis import get_redis_connection
from app.jobs.outbound_call_jobs import execute_next_outbound_call_job
from app.jobs.surveymonkey_jobs import sync_campaign_survey_responses_job
from app.schemas.job import JobQueuedRead
from app.services.job_queue_service import (
    enqueue_unique_active_job,
    get_job_status_value,
)

from app.services.surveymonkey_sync_service import sync_campaign_survey_responses_for_campaign
from app.services.surveymonkey_campaign_template_service import (
    create_campaign_survey_from_template,
    list_surveymonkey_template_surveys,
)
from app.services.surveymonkey_distribution_service import (
    create_campaign_survey_message,
    prepare_campaign_survey_recipients,
    send_campaign_survey_message,
)

from app.core.database import get_session



router = APIRouter(prefix="/campaigns", tags=["Campaigns"])



@router.post("", response_model=CampaignRead, status_code=201)
def create_campaign(
    campaign: CampaignCreate,
    session: Session = Depends(get_session)
):
    """Create and persist a new campaign."""
    return create_campaign_service(campaign, session)



@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """List campaigns with pagination."""
    return list_campaigns_service(session, limit, offset=offset)


@router.get("/survey/templates", response_model=list[SurveyMonkeyTemplateRead])
def list_survey_templates():
    """List SurveyMonkey surveys named as templates (titles starting with TEMPLATE -)."""
    try:
        return list_surveymonkey_template_surveys()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_session)
):
    """Fetch a single campaign by id, raising 404 if it does not exist."""
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign is not found")

    return campaign


@router.patch("/{campaign_id}/status", response_model=CampaignRead)
def update_campaign_lifecycle_status(
    campaign_id: str,
    status_update: CampaignStatusUpdate,
    session: Session = Depends(get_session),
):
    """Update a campaign's lifecycle status (e.g. draft/active/completed).

    Raises 404 if the campaign does not exist. Persists the status
    transition via ``update_campaign_status``.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return update_campaign_status(
        campaign=campaign,
        status_update=status_update,
        session=session,
    )


@router.get("/{campaign_id}/summary", response_model=CampaignSummaryRead)
def get_campaign_summary(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Return aggregate summary stats (e.g. counts by status) for a campaign.

    Raises 404 if the campaign does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return get_campaign_summary_service(
        campaign_id=campaign_id,
        session=session,
    )


@router.post("/{campaign_id}/candidates", response_model=list[CampaignCandidateRead], status_code=201)
def add_candidates_to_campaign(
    campaign_id: str,
    candidates: list[CampaignCandidateCreate],
    session: Session = Depends(get_session)
):
    """Add one or more candidates to a campaign.

    Raises 404 if the campaign does not exist. Persists the new
    candidate rows via ``add_campaign_candidates``.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
         raise HTTPException(status_code=404, detail="Campaign not found")

    return add_campaign_candidates(campaign_id, candidates, session)


@router.post("/{campaign_id}/candidates/upload", response_model=list[CampaignCandidateRead], status_code=201)
async def upload_candidates_to_campaign(
    campaign_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session)
):
    """Add candidates to a campaign by uploading a file (e.g. CSV/XLSX).

    Raises 404 if the campaign does not exist. Reads the full upload
    into memory, then parses it into candidate records, defaulting the
    tool/campaign name from the campaign when not present in the file.
    Raises 400 if the file cannot be parsed (e.g. bad format/headers).
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    file_bytes = await file.read()

    try:
        candidates = parse_candidate_upload(
            file_bytes=file_bytes,
            filename=file.filename or "",
            default_tool_name=campaign.tool_name,
            default_campaign_name=campaign.name,
        )
    except ValueError as exc:
        # parse_candidate_upload raises ValueError for malformed/unsupported
        # uploads (bad extension, missing required columns, etc.).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return add_campaign_candidates(
        campaign_id=campaign_id,
        candidates_data=candidates,
        session=session,
    )

@router.get("/{campaign_id}/candidates", response_model=list[CampaignCandidateRead])
def list_campaign_candidates(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """List candidates belonging to a campaign, paginated.

    Raises 404 if the campaign does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return list_campaign_candidates_service(
        campaign_id,
        session,
        limit=limit,
        offset=offset
    )



@router.patch("/{campaign_id}/candidates/{candidate_id}/survey-status", response_model=CampaignCandidateRead)
def update_candidate_survey_status(
    campaign_id: str,
    candidate_id: str,
    status_update: CampaignCandidateSurveyStatusUpdate,
    session: Session = Depends(get_session),
):
    """Update a candidate's survey status within a campaign (e.g. sent/responded).

    Raises 404 if the campaign or the candidate does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    candidate = get_campaign_candidate_by_id(campaign_id, candidate_id, session)

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return update_campaign_candidate_survey_status(
        candidate,
        status_update,
        session
    )


@router.get("/{campaign_id}/non-responders", response_model=list[CampaignCandidateRead])
def list_eligible_non_responders(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List candidates in a campaign who are eligible for outbound
    follow-up because they have not responded to the survey.

    Raises 404 if the campaign does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return list_eligible_non_responders_service(
       campaign_id,
       session,
       limit=limit,
       offset=offset
    )


@router.post("/{campaign_id}/survey/sync-responses", response_model=CampaignSurveySyncResponse)
def sync_campaign_survey_responses(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Synchronously fetch and store SurveyMonkey survey responses for a campaign.

    Calls out to the SurveyMonkey API and writes matched responses to
    the database. Raises 400 if the campaign is not configured for
    SurveyMonkey sync (e.g. missing survey/collector ids) or the input
    is otherwise invalid, and 500 if the sync itself fails (e.g.
    SurveyMonkey API error).
    """
    try:
        return sync_campaign_survey_responses_for_campaign(
            campaign_id=campaign_id,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{campaign_id}/survey/create-from-template", response_model=CampaignRead)
def create_campaign_survey(
    campaign_id: str,
    template_request: CampaignSurveyTemplateCreate | None = None,
    session: Session = Depends(get_session),
):
    """Copy the configured SurveyMonkey campaign template and attach it to this campaign."""
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        return create_campaign_survey_from_template(
            campaign=campaign,
            session=session,
            template_survey_id=template_request.template_survey_id if template_request else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/{campaign_id}/survey/message",
    response_model=CampaignSurveyMessageCreateResponse,
    status_code=201,
)
def create_campaign_survey_message_draft(
    campaign_id: str,
    message_request: CampaignSurveyMessageCreate,
    session: Session = Depends(get_session),
):
    """Create an unsent SurveyMonkey collector message draft for this campaign."""
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        return create_campaign_survey_message(
            campaign=campaign,
            collector_id=message_request.collector_id,
            collector_name=message_request.collector_name,
            subject=message_request.subject,
            body=message_request.body,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/{campaign_id}/survey/recipients/prepare",
    response_model=CampaignSurveyRecipientsPrepareResponse,
)
def prepare_campaign_survey_message_recipients(
    campaign_id: str,
    recipient_request: CampaignSurveyRecipientsPrepare,
    session: Session = Depends(get_session),
):
    """Add eligible campaign candidates to a SurveyMonkey message without sending emails."""
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        return prepare_campaign_survey_recipients(
            campaign=campaign,
            collector_id=recipient_request.collector_id,
            message_id=recipient_request.message_id,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/{campaign_id}/survey/message/send",
    response_model=CampaignSurveyMessageSendResponse,
)
def send_campaign_survey_message_to_prepared_recipients(
    campaign_id: str,
    send_request: CampaignSurveyMessageSend,
    session: Session = Depends(get_session),
):
    """Send a prepared SurveyMonkey message after explicit confirmation."""
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        return send_campaign_survey_message(
            campaign=campaign,
            collector_id=send_request.collector_id,
            message_id=send_request.message_id,
            confirm_send=send_request.confirm_send,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{campaign_id}/outbound/build-queue", response_model=list[OutboundCallAttemptRead], status_code=201)
def build_outbound_queue(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Build the initial outbound call queue (attempts) for eligible non-responders.

    Raises 404 if the campaign does not exist. Creates new
    ``OutboundCallAttempt`` rows via ``build_outbound_call_queue_service``.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return build_outbound_call_queue_service(campaign_id, session)

@router.post("/{campaign_id}/outbound/retry-queue", response_model=list[OutboundCallAttemptRead], status_code=201)
def build_outbound_retry_queue(
    campaign_id: str,
    session: Session = Depends(get_session),
    max_attempts: int = Query(3, ge=1, le=10),
):
    """Build a retry queue of outbound call attempts for candidates
    that failed/were unreachable, up to ``max_attempts`` tries each.

    Raises 404 if the campaign does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return build_outbound_retry_queue_service(
        campaign_id=campaign_id,
        session=session,
        max_attempts=max_attempts,
    )


@router.get("/{campaign_id}/outbound/attempts", response_model=list[OutboundCallAttemptRead])
def list_outbound_attempts(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List outbound call attempts for a campaign, paginated.

    Raises 404 if the campaign does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return list_outbound_call_attempts_service(
        campaign_id,
        session,
        limit=limit,
        offset=offset,
    )


@router.get("/{campaign_id}/outbound/attempts/next", response_model=OutboundCallAttemptRead)
def get_next_outbound_attempt(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Fetch the next queued outbound call attempt for a campaign.

    Used by callers (e.g. the outbound call worker) to pick up the next
    attempt to dial. Raises 404 if the campaign does not exist, or if
    no attempt is currently queued.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    attempt = get_next_queued_outbound_attempt(
        campaign_id=campaign_id,
        session=session,
    )

    if not attempt:
        raise HTTPException(status_code=404, detail="No queued outbound attempt found")

    return attempt

@router.patch(
    "/{campaign_id}/outbound/attempts/{attempt_id}/status",
    response_model=OutboundCallAttemptRead,
)
def update_outbound_attempt_status(
    campaign_id: str,
    attempt_id: str,
    status_update: OutboundCallAttemptStatusUpdate,
    session: Session = Depends(get_session),
):
    """Update the status of a specific outbound call attempt (e.g. after a call completes).

    Raises 404 if the campaign or the attempt does not exist.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    attempt = get_outbound_call_attempt_by_id(
        campaign_id=campaign_id,
        attempt_id=attempt_id,
        session=session,
    )

    if not attempt:
        raise HTTPException(status_code=404, detail="Outbound call attempt not found")

    return update_outbound_call_attempt_status(
        attempt=attempt,
        status_update=status_update,
        session=session,
    )


@router.post(
    "/{campaign_id}/survey/sync-responses/jobs",
    response_model=JobQueuedRead,
    status_code=202,
)
def enqueue_campaign_survey_sync_job(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Enqueue a background job to sync SurveyMonkey survey responses for a campaign.

    Raises 404 if the campaign does not exist, and 400 if it lacks the
    SurveyMonkey survey/collector configuration needed to sync. Uses
    ``enqueue_unique_active_job`` so that a second call while a sync job
    for this campaign is already active/queued reuses that job instead
    of enqueueing a duplicate.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if not campaign.survey_id or not campaign.surveymonkey_collector_id:
        raise HTTPException(
            status_code=400,
            detail="Campaign does not have SurveyMonkey survey and collector configured",
        )

    queue = get_default_queue()
    redis_connection = get_redis_connection()
    job = enqueue_unique_active_job(
        queue=queue,
        redis_connection=redis_connection,
        lock_key=f"campaign:{campaign_id}:survey-sync-job",
        func=sync_campaign_survey_responses_job,
        args=(campaign_id,),
        job_timeout=600,
        result_ttl=3600,
        lock_ttl=900,
    )

    return {
        "job_id": job.id,
        "status": get_job_status_value(job),
    }


@router.post(
    "/{campaign_id}/outbound/execute-next/jobs",
    response_model=JobQueuedRead,
    status_code=202,
)
def enqueue_next_outbound_call_job(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    """Enqueue a background job to place the next queued outbound call for a campaign.

    Raises 404 if the campaign does not exist. Uses
    ``enqueue_unique_active_job`` so concurrent calls do not enqueue
    duplicate "execute next" jobs for the same campaign.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    queue = get_default_queue()
    redis_connection = get_redis_connection()
    job = enqueue_unique_active_job(
        queue=queue,
        redis_connection=redis_connection,
        lock_key=f"campaign:{campaign_id}:outbound-execute-next-job",
        func=execute_next_outbound_call_job,
        args=(campaign_id,),
        job_timeout=300,
        result_ttl=3600,
        lock_ttl=600,
    )

    return {
        "job_id": job.id,
        "status": get_job_status_value(job),
    }
