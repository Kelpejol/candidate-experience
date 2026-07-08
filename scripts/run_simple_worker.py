"""Run a simple, single-process RQ worker for the default job queue.

Starts an `rq.SimpleWorker` (no forking, jobs run in-process) that pulls
and executes jobs from the application's default queue until stopped.
Intended for local development/testing rather than production use, where
a forking worker would normally be preferred for isolation.

Run with: python scripts/run_simple_worker.py
"""

from rq import SimpleWorker

from app.core.queue import get_default_queue
from app.core.redis import get_redis_connection


queue = get_default_queue()

worker = SimpleWorker(
    [queue],
    connection=get_redis_connection(),
)

worker.work()  # blocks, polling the queue for jobs until interrupted