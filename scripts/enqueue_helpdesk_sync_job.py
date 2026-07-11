"""Manually enqueue a helpdesk ticket mirror sync job.

Pushes a `sync_helpdesk_ticket_mirror_job` onto the default RQ queue and
prints the resulting job's ID and status. A worker (e.g.
run_simple_worker.py) must be running to actually process it.

Run with: python scripts/enqueue_helpdesk_sync_job.py
"""

from app.core.queue import get_default_queue
from app.jobs.helpdesk_sync_jobs import sync_helpdesk_ticket_mirror_job


queue = get_default_queue()
job = queue.enqueue(
    sync_helpdesk_ticket_mirror_job,
    job_timeout=600,   # allow up to 10 minutes for the sync to run
    result_ttl=3600,   # keep the job result around for 1 hour
)

print(f"Queued helpdesk mirror sync job: {job.id}")
print(f"Status: {job.get_status()}")
