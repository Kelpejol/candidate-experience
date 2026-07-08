"""RQ (Redis Queue) job queue setup.

Provides access to the application's default background job queue, used
for enqueuing work such as SurveyMonkey sync and outbound call jobs.
"""

from rq import Queue

from app.core.config import get_settings
from app.core.redis import get_redis_connection


def get_default_queue() -> Queue:
    """Build the default RQ `Queue`, named per settings and backed by Redis.

    Side effect: opens a new Redis connection (via `get_redis_connection`)
    each call. Returns the `Queue` instance used to enqueue background jobs.
    """
    settings = get_settings()

    return Queue(
        name=settings.rq_default_queue,
        connection=get_redis_connection(),
    )