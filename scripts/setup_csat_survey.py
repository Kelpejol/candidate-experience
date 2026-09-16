"""One-time: create the standing CSAT survey + email collector from the CSAT
template, and print the ids to put in .env.

This makes REAL SurveyMonkey objects (a live survey and collector). Run it once
when you're ready to turn CSAT on, then set:

    SURVEYMONKEY_CSAT_SURVEY_ID=<printed survey id>
    SURVEYMONKEY_CSAT_COLLECTOR_ID=<printed collector id>

Requires SURVEYMONKEY_ACCESS_TOKEN and SURVEYMONKEY_CSAT_TEMPLATE_SURVEY_ID.

Run with: python scripts/setup_csat_survey.py
"""

from app.services.csat_service import create_standing_csat_survey_from_template

result = create_standing_csat_survey_from_template()

print("Standing CSAT survey created.")
print(f"  SURVEYMONKEY_CSAT_SURVEY_ID={result['survey_id']}")
print(f"  SURVEYMONKEY_CSAT_COLLECTOR_ID={result['collector_id']}")
print("\nAdd those to .env, then use POST /csat/jobs/send-pending and")
print("POST /csat/jobs/sync (or the RQ jobs) to send and reconcile CSATs.")
