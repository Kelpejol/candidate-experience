"""Tests for the outbound retry service's is_retryable_outbound_status helper.

Verifies that outbound call attempt statuses which indicate a failed or
missed call (e.g. no_answer, busy) are classified as retryable, while
statuses representing in-progress, successful, or terminal outcomes are not.
"""

import pytest

from app.services.outbound_retry_service import is_retryable_outbound_status


@pytest.mark.parametrize(
    "status",
    [
        "no_answer",
        "busy",
        "voicemail",
        "failed",
    ],
)
def test_is_retryable_outbound_status_returns_true_for_retryable_statuses(status):
    """Verifies that statuses indicating a missed/failed call attempt are considered retryable."""
    assert is_retryable_outbound_status(status) is True


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "calling",
        "answered",
        "responded_by_call",
        "opted_out",
        "handed_off_to_human",
    ],
)
def test_is_retryable_outbound_status_returns_false_for_non_retryable_statuses(status):
    """Verifies that in-progress, successful, and terminal (opted out / handed off) statuses are not retryable."""
    assert is_retryable_outbound_status(status) is False