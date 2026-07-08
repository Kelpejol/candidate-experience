"""Manually enqueue a SurveyMonkey response sync job for a campaign.

Pushes a `sync_campaign_survey_responses_job` onto the default RQ queue
for the given campaign ID and prints the resulting job's ID and status.
A worker (e.g. run_simple_worker.py) must be running to actually process it.

Run with: python scripts/enqueue_surveymonkey_sync_job.py <campaign_id>
"""

import sys

from app.core.queue import get_default_queue
from app.jobs.surveymonkey_jobs import sync_campaign_survey_responses_job


if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/enqueue_surveymonkey_sync_job.py <campaign_id>")

campaign_id = sys.argv[1]

queue = get_default_queue()
job = queue.enqueue(
    sync_campaign_survey_responses_job,
    campaign_id,
    job_timeout=600,  # allow up to 10 minutes for the sync to run
    result_ttl=3600,  # keep the job result around for 1 hour
)

print(f"Queued SurveyMonkey sync job: {job.id}")
print(f"Status: {job.get_status()}")