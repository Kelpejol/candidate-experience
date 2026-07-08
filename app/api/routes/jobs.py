"""API routes for querying the status of background jobs.

Jobs (SurveyMonkey sync, outbound calls, retries, etc.) are executed
asynchronously via RQ against Redis; this module exposes a way to poll
a job's status/result/error by id.
"""

from fastapi import APIRouter, HTTPException
from rq.job import Job

from app.core.redis import get_redis_connection
from app.schemas.job import JobStatusRead
from app.services.job_queue_service import get_job_status_value


router = APIRouter(prefix="/jobs", tags=["Jobs"])


def get_friendly_job_error(exc_info: str | None) -> str | None:
    """Extract a short, human-readable error message from an RQ traceback.

    RQ's ``exc_info`` is a full traceback string; the last non-empty
    line is typically the exception type/message, which is more
    useful to surface to API consumers than the whole traceback.
    Returns None if there is no traceback.
    """
    if not exc_info:
        return None

    lines = [line.strip() for line in exc_info.splitlines() if line.strip()]
    return lines[-1] if lines else "Job failed"


def serialize_job_status(job: Job) -> JobStatusRead:
    """Convert an RQ ``Job`` into a ``JobStatusRead`` response model.

    Only populates ``result`` once the job has finished, and only
    populates ``error``/``error_traceback`` once the job has failed.
    ``return_value(refresh=True)`` forces a fresh read from Redis
    rather than any cached value on the job instance.
    """
    result = None
    error = None
    error_traceback = None

    if job.is_finished:
        result = job.return_value(refresh=True)

    if job.is_failed:
        error = get_friendly_job_error(job.exc_info)
        error_traceback = job.exc_info

    return JobStatusRead(
        job_id=job.id,
        status=get_job_status_value(job),
        created_at=getattr(job, "created_at", None),
        enqueued_at=getattr(job, "enqueued_at", None),
        started_at=getattr(job, "started_at", None),
        ended_at=getattr(job, "ended_at", None),
        result=result,
        error=error,
        error_traceback=error_traceback,
    )


@router.get("/{job_id}", response_model=JobStatusRead)
def get_job_status(job_id: str):
    """Fetch and return the status/result of a background job by id.

    ``Job.fetch`` raises if the job id is unknown or has expired from
    Redis (result TTL passed); any such exception is mapped to a 404.
    """
    try:
        job = Job.fetch(
            job_id,
            connection=get_redis_connection(),
        )
    except Exception as exc:
        # rq raises NoSuchJobError (or similar) for unknown/expired job ids;
        # broadly catching here to translate any lookup failure into a 404.
        raise HTTPException(status_code=404, detail="Job not found") from exc

    return serialize_job_status(job)
