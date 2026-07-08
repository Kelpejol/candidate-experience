"""Trivial job used to exercise the Redis/RQ job queue wiring end-to-end.

Not tied to any business logic; enqueuing this job lets the queue setup
(app/core/queue.py, app/core/redis.py) and worker be smoke-tested without
touching the database or external services.
"""


def add_numbers(a: int, b: int) -> int:
    """Return a + b. No side effects; used as a minimal RQ smoke-test job."""
    return a + b