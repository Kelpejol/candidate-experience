"""Continuously enqueue the helpdesk pipeline job on a cron schedule.

This is the "wire it to a cadence" piece: instead of a script you run by
hand, this process runs forever (like run_simple_worker.py) and enqueues
run_helpdesk_pipeline_job every time HELPDESK_PIPELINE_CRON fires (default
every 5 minutes). A worker must be running separately to pick the jobs up.

This process only enqueues — it never touches Zoho or the DB directly, so
if it crashes the worst case is a missed cycle, not a corrupted run.

When the Zoho admin unblocks the workflow-rule webhook, that becomes the
real-time trigger and this scheduler becomes the reconciliation fallback
(still useful — catches anything the webhook missed) rather than the
primary path. No code changes needed either way.

Alternative for production: instead of running this as a long-lived
process, a system cron entry can call
`python scripts/enqueue_helpdesk_pipeline_job.py` directly on the same
cadence — whichever fits your ops setup better.

Run with: python scripts/run_helpdesk_scheduler.py
Stop with Ctrl+C.
"""

import time
from datetime import datetime

from croniter import croniter

from app.core.config import get_settings
from app.core.queue import get_default_queue
from app.jobs.helpdesk_ai_jobs import run_helpdesk_pipeline_job


def main() -> None:
    settings = get_settings()
    cron_expression = settings.helpdesk_pipeline_cron
    queue = get_default_queue()

    print(f"Helpdesk pipeline scheduler started. Cron: '{cron_expression}'")
    schedule = croniter(cron_expression, datetime.now())

    while True:
        next_run = schedule.get_next(datetime)
        sleep_seconds = max(0.0, (next_run - datetime.now()).total_seconds())
        time.sleep(sleep_seconds)

        job = queue.enqueue(run_helpdesk_pipeline_job, job_timeout=900, result_ttl=3600)
        print(f"[{datetime.now().isoformat(timespec='seconds')}] Enqueued pipeline job {job.id}")


if __name__ == "__main__":
    main()
