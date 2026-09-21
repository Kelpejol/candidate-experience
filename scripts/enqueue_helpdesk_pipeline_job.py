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
from app.core.redis import get_redis_connection
from app.jobs.helpdesk_ai_jobs import run_helpdesk_pipeline_job
from app.services.job_queue_service import enqueue_unique_active_job, get_job_status_value


queue = get_default_queue()
redis_connection = get_redis_connection()
job = enqueue_unique_active_job(
    queue=queue,
    redis_connection=redis_connection,
    lock_key="helpdesk:pipeline:scheduled-job",
    func=run_helpdesk_pipeline_job,
    job_timeout=900,    # classification + drafting per ticket can add up
    result_ttl=3600,
    lock_ttl=900,
)

print(f"Queued helpdesk pipeline job: {job.id}")
print(f"Status: {get_job_status_value(job)}")
