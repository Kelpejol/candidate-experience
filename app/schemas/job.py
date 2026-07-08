"""Pydantic schemas for the background job queue API (RQ-backed jobs).

Covers the response shape returned right after a job is enqueued and the
shape used to report a job's current status, timing, result, and error
details when polled later.
"""

from pydantic import BaseModel
from datetime import datetime


class JobQueuedRead(BaseModel):
    """Response shape returned immediately after a job is enqueued onto the queue."""

    job_id: str
    status: str


class JobStatusRead(BaseModel):
    """Response shape for polling a job's status.

    Timing fields are populated progressively as the job moves through the
    RQ lifecycle (created -> enqueued -> started -> ended) and remain None
    until that stage is reached. `result` holds the job's return value once
    finished; `error`/`error_traceback` are populated only if the job failed.
    """

    job_id: str
    status: str
    created_at: datetime | None = None
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: dict | None = None
    error: str | None = None
    error_traceback: str | None = None
