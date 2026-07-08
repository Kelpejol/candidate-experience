"""Enqueue a trivial `add_numbers` job to smoke-test the job queue.

Pushes a simple test job onto the default RQ queue and prints its ID and
status. A worker (e.g. run_simple_worker.py) must be running to process it.

Run with: python scripts/enqueue_test_job.py
"""

from app.core.queue import get_default_queue
from app.jobs.test_jobs import add_numbers


queue = get_default_queue()
job = queue.enqueue(add_numbers, 2, 3)

print(f"Queued job: {job.id}")
print(f"Status: {job.get_status()}")