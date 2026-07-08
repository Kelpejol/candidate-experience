"""Redis connection helper.

Provides a factory for Redis client connections built from the configured
`redis_url`, used by the job queue and related background job tooling.
"""

from redis import Redis

from app.core.config import get_settings


def get_redis_connection() -> Redis:
    """Create a new `Redis` client from the configured `redis_url`.

    Side effect: opens a connection to Redis. `decode_responses=False` keeps
    responses as bytes, which is what RQ expects for job serialization.
    """
    settings = get_settings()

    return Redis.from_url(
        settings.redis_url,
        decode_responses=False,
    )