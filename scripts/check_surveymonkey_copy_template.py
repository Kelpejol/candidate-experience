"""Validate SurveyMonkey template cloning with a temporary test survey.

This is a live integration check. It creates one real draft survey whose
title starts with "TEST - DELETE ME", verifies the cloned page/question
counts, and deletes the test survey by default.

Run with:
    PYTHONPATH=. .venv/bin/python scripts/check_surveymonkey_copy_template.py

Use --keep only when you intentionally want to inspect the generated draft
inside SurveyMonkey.
"""

import argparse
from datetime import UTC, datetime

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.surveymonkey_campaign_template_service import (
    clone_surveymonkey_survey_from_template,
)


parser = argparse.ArgumentParser()
parser.add_argument(
    "--keep",
    action="store_true",
    help="Keep the generated TEST survey instead of deleting it.",
)
args = parser.parse_args()

settings = get_settings()

if not settings.surveymonkey_access_token:
    raise RuntimeError("SURVEYMONKEY_ACCESS_TOKEN is not configured")

if not settings.surveymonkey_campaign_template_survey_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_TEMPLATE_SURVEY_ID is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

title = (
    "TEST - DELETE ME - API Template Clone Check - "
    f"{datetime.now(UTC).isoformat(timespec='seconds')}"
)
created_survey_id = clone_surveymonkey_survey_from_template(
    client=client,
    template_survey_id=settings.surveymonkey_campaign_template_survey_id,
    title=title,
    variables={
        "campaign_name": "API Clone Validation Campaign",
        "campaign_title": "API Clone Validation Campaign",
        "assessment_name": "API Clone Validation Campaign",
        "tool_name": "FOT",
    },
)

template_details = client.get_survey_details(settings.surveymonkey_campaign_template_survey_id)
created_details = client.get_survey_details(created_survey_id)

print("SurveyMonkey template clone worked")
print(f"template_survey_id: {settings.surveymonkey_campaign_template_survey_id}")
print(f"created_survey_id: {created_survey_id}")
print(f"title: {title}")
print(f"template_page_count: {template_details.get('page_count')}")
print(f"created_page_count: {created_details.get('page_count')}")
print(f"template_question_count: {template_details.get('question_count')}")
print(f"created_question_count: {created_details.get('question_count')}")

if template_details.get("page_count") != created_details.get("page_count"):
    raise RuntimeError("Created survey page count does not match template page count")

if template_details.get("question_count") != created_details.get("question_count"):
    raise RuntimeError("Created survey question count does not match template question count")

if args.keep:
    print("kept_test_survey: true")
else:
    client.delete_survey(created_survey_id)
    print("deleted_test_survey: true")
