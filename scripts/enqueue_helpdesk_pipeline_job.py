"""Manually enqueue the full helpdesk pipeline job (sync + AI decisions).

Pushes `run_helpdesk_pipeline_job` onto the default RQ queue and prints the
resulting job's ID and status. A worker (e.g. run_simple_worker.py) must be
running to actually process it.

To automate this instead of running it by hand, schedule this same enqueue
on a cron cadence (e.g. every 5 minutes) until the Zoho webhook is unblocked
by IT — at that point the webhook can enqueue this job directly on ticket
create/update instead of polling.

Run with: python scripts/enqueue_helpdesk_pipeline_job.py
"""

from app.core.queue import get_default_queue
from app.jobs.helpdesk_ai_jobs import run_helpdesk_pipeline_job


queue = get_default_queue()
job = queue.enqueue(
    run_helpdesk_pipeline_job,
    job_timeout=900,   # classification + drafting per ticket can add up
    result_ttl=3600,
)

print(f"Queued helpdesk pipeline job: {job.id}")
print(f"Status: {job.get_status()}")
