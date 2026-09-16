"""Run a production-grade RQ worker for the default job queue.

Starts an `rq.Worker` (forks a fresh process per job) that pulls and executes
jobs from the application's default queue until stopped. One crashing or
hanging job can't take the whole worker down with it — the standard
production choice, replacing `run_simple_worker.py` (in-process, no
isolation, its own docstring always said "not for production use").

Run one or more of these side by side to raise job throughput — each
instance independently pulls from the same queue, so N workers roughly
divides job-processing time by N. RQ's own default `Worker` naming keeps
them from colliding.

Run with: python scripts/run_worker.py
"""

from rq import Worker

from app.core.queue import get_default_queue
from app.core.redis import get_redis_connection


queue = get_default_queue()

worker = Worker(
    [queue],
    connection=get_redis_connection(),
)

worker.work()  # blocks, polling the queue for jobs until interrupted
