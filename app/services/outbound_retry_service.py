"""Classifies terminal OutboundCallAttempt statuses as retryable or not.

Used by campaign_service.build_outbound_retry_queue to decide whether a
candidate's most recent outbound call attempt should be requeued for another
attempt (up to that caller's own max-attempts limit).
"""

# Terminal outcomes where the candidate was not reached/did not respond, so
# another attempt is worth making (subject to the caller's attempt-count cap).
RETRYABLE_OUTBOUND_STATUSES = {
    "no_answer",
    "busy",
    "voicemail",
    "failed",
}

# In-flight statuses ("queued", "calling") and terminal statuses where the
# candidate was reached or the case is otherwise closed, so retrying would be
# wasteful or incorrect. Not referenced directly by is_retryable_outbound_status
# (which only checks RETRYABLE_OUTBOUND_STATUSES); kept here for documentation
# of the full status space.
NON_RETRYABLE_OUTBOUND_STATUSES = {
    "queued",
    "calling",
    "answered",
    "responded_by_call",
    "opted_out",
    "handed_off_to_human",
}


def is_retryable_outbound_status(status: str) -> bool:
    """Return True if an outbound call attempt in `status` should be retried."""
    return status in RETRYABLE_OUTBOUND_STATUSES