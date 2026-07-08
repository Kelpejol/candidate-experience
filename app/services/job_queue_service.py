"""Helpers for enqueuing background jobs onto the Redis-backed RQ queue.

Centralizes RQ job-status inspection and a "de-duplicated enqueue" pattern
that prevents the same logical job (e.g. a SurveyMonkey sync for a given
campaign) from being queued multiple times concurrently, using a Redis key
as a distributed lock/pointer to the in-flight job.
"""

from collections.abc import Callable

from redis import Redis
from rq import Queue
from rq.job import Job


# RQ job statuses that indicate the job has not finished/failed yet, i.e.
# a duplicate enqueue should be skipped in favor of the existing job.
ACTIVE_JOB_STATUSES = {"queued", "started", "deferred", "scheduled"}


def get_job_status_value(job: Job) -> str:
    """
    Return an RQ job's current status as a plain string.

    Refreshes the job's status from Redis before reading it. Handles both
    enum-like status objects (with a `.value`) and plain strings, since RQ's
    return type has varied across versions.
    """
    status = job.get_status(refresh=True)
    return status.value if hasattr(status, "value") else str(status)


def enqueue_unique_active_job(
    *,
    queue: Queue,
    redis_connection: Redis,
    lock_key: str,
    func: Callable,
    args: tuple = (),
    job_timeout: int = 600,
    result_ttl: int = 3600,
    lock_ttl: int = 900,
) -> Job:
    """
    Enqueue `func(*args)` on `queue`, but only if no other active job is
    already tracked under `lock_key`; otherwise return that existing job.

    `lock_key` in Redis stores the id of the most recently enqueued job for
    this logical task. If it points to a job that is still queued/running,
    the existing job is returned unchanged (no new enqueue). If the lock is
    stale (missing job, or job finished/failed) it is cleared and a new job
    is enqueued.

    Side effects: may enqueue a job on `queue`; writes/deletes `lock_key` in
    Redis (set with `lock_ttl` expiry after a successful enqueue).
    """
    existing_job_id = redis_connection.get(lock_key)

    if existing_job_id:
        # Redis clients may return bytes depending on decode_responses config.
        if isinstance(existing_job_id, bytes):
            existing_job_id = existing_job_id.decode("utf-8")

        try:
            existing_job = Job.fetch(existing_job_id, connection=redis_connection)
            if get_job_status_value(existing_job) in ACTIVE_JOB_STATUSES:
                return existing_job
        except Exception:
            # Job data may have expired/been evicted from Redis; treat the
            # lock as stale rather than failing the enqueue.
            pass

        redis_connection.delete(lock_key)

    job = queue.enqueue(
        func,
        *args,
        job_timeout=job_timeout,
        result_ttl=result_ttl,
    )
    # Point the lock at the new job so subsequent callers dedupe against it.
    redis_connection.set(lock_key, job.id, ex=lock_ttl)

    return job
