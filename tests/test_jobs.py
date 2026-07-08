"""Tests for the jobs API's serialize_job_status helper.

Verifies that an RQ Job object is converted into a JobStatusRead schema
with correct status/timing fields and a human-friendly error message
extracted from the job's traceback.
"""

from datetime import datetime

from app.api.routes.jobs import serialize_job_status


class FakeStatus:
    """Minimal stand-in for an RQ job status object exposing just `.value`."""

    value = "failed"


class FakeJob:
    """Fake RQ Job representing a failed job, with a multi-line traceback in exc_info."""

    id = "job_123"
    is_finished = False
    is_failed = True
    created_at = datetime(2026, 7, 8, 10, 0, 0)
    enqueued_at = datetime(2026, 7, 8, 10, 0, 1)
    started_at = datetime(2026, 7, 8, 10, 0, 2)
    ended_at = datetime(2026, 7, 8, 10, 0, 3)
    # Multi-line traceback: serialize_job_status should surface only the
    # last line as the "friendly" error while keeping the full text too.
    exc_info = "Traceback line\nRuntimeError: Something broke"

    def get_status(self, refresh=True):
        return FakeStatus()


def test_serialize_job_status_includes_timing_and_friendly_error():
    """Verifies that serializing a failed job includes its status/timestamps and derives a friendly error (last traceback line) plus the full traceback."""
    serialized = serialize_job_status(FakeJob())

    assert serialized.job_id == "job_123"
    assert serialized.status == "failed"
    assert serialized.created_at == datetime(2026, 7, 8, 10, 0, 0)
    assert serialized.enqueued_at == datetime(2026, 7, 8, 10, 0, 1)
    assert serialized.started_at == datetime(2026, 7, 8, 10, 0, 2)
    assert serialized.ended_at == datetime(2026, 7, 8, 10, 0, 3)
    assert serialized.error == "RuntimeError: Something broke"
    assert serialized.error_traceback == FakeJob.exc_info
