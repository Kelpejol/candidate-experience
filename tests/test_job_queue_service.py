"""Tests for the Redis-backed job queue dedup helper (enqueue_unique_active_job).

Verifies that a job lock stored in Redis is honored so a new job is not
enqueued while a previously enqueued job for the same lock key is still
active.
"""

from app.services import job_queue_service
from app.services.job_queue_service import enqueue_unique_active_job


class FakeQueuedJob:
    """Fake RQ Job that reports an active ("queued") status."""

    id = "existing_job"

    def get_status(self, refresh=True):
        class Status:
            value = "queued"

        return Status()


class FakeRedis:
    """In-memory stand-in for a Redis connection, pre-seeded with an existing lock entry."""

    def __init__(self):
        # Simulates a lock already held by a previously enqueued job.
        self.values = {"lock_key": b"existing_job"}
        self.deleted = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


class FakeQueue:
    """Fake RQ Queue that fails the test if a new job is ever enqueued."""

    def enqueue(self, *args, **kwargs):
        raise AssertionError("Should not enqueue when active job exists")


def test_enqueue_unique_active_job_returns_existing_active_job(monkeypatch):
    """Verifies that enqueue_unique_active_job returns the existing job (without enqueueing a new one) when the lock key points to a still-active job."""
    class FakeJobFetcher:
        """Stand-in for rq.job.Job used to intercept Job.fetch() lookups."""

        @classmethod
        def fetch(cls, job_id, connection):
            # The lock value in FakeRedis is the id this fetch should be
            # called with; asserting it here catches lock-key mix-ups.
            assert job_id == "existing_job"
            return FakeQueuedJob()

    monkeypatch.setattr(job_queue_service, "Job", FakeJobFetcher)

    job = enqueue_unique_active_job(
        queue=FakeQueue(),
        redis_connection=FakeRedis(),
        lock_key="lock_key",
        func=lambda: None,
    )

    assert job.id == "existing_job"
