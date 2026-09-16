"""Continuously enqueue the campaign orchestration job on a cron schedule.

Mirrors run_helpdesk_scheduler.py: a long-lived process that enqueues
run_campaign_orchestration_job every time CAMPAIGN_ORCHESTRATION_CRON fires
(default every 10 minutes). A worker must be running separately to pick the
jobs up. This process only enqueues — it never touches the DB/SurveyMonkey/
telephony directly, so a crash costs at most a missed cycle.

Run with: python scripts/run_campaign_scheduler.py
Stop with Ctrl+C.
"""

import time
from datetime import datetime

from croniter import croniter

from app.core.config import get_settings
from app.core.queue import get_default_queue
from app.jobs.campaign_orchestration_jobs import run_campaign_orchestration_job


def main() -> None:
    settings = get_settings()
    cron_expression = settings.campaign_orchestration_cron
    queue = get_default_queue()

    print(f"Campaign orchestration scheduler started. Cron: '{cron_expression}'")
    schedule = croniter(cron_expression, datetime.now())

    while True:
        next_run = schedule.get_next(datetime)
        sleep_seconds = max(0.0, (next_run - datetime.now()).total_seconds())
        time.sleep(sleep_seconds)

        job = queue.enqueue(
            run_campaign_orchestration_job, job_timeout=900, result_ttl=3600
        )
        print(f"[{datetime.now().isoformat(timespec='seconds')}] "
              f"Enqueued campaign orchestration job {job.id}")


if __name__ == "__main__":
    main()
