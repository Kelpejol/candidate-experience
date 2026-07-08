"""Sanity-check the Redis connection and default RQ queue.

Connects to Redis using the configured `redis_url`, pings it, and prints
the name of the application's default job queue. Useful for quickly
verifying that Redis is reachable and configured correctly.

Run with: python scripts/check_redis_queue.py
"""

from app.core.queue import get_default_queue
from app.core.redis import get_redis_connection


redis_connection = get_redis_connection()
redis_connection.ping()  # raises if Redis is unreachable or misconfigured

queue = get_default_queue()

print("Redis connection is working")
print(f"Queue name: {queue.name}")