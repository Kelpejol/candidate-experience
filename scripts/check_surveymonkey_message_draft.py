"""Validate creating a SurveyMonkey email collector and unsent message draft.

This is a live integration check. It creates one temporary survey whose
title starts with "TEST - DELETE ME", creates an email collector and draft
message on that survey, verifies the ids, and deletes the temporary survey.

No recipients are added and no emails are sent.

Run with:
    PYTHONPATH=. .venv/bin/python scripts/check_surveymonkey_message_draft.py
"""

from datetime import UTC, datetime
from requests import HTTPError

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.surveymonkey_distribution_service import extract_surveymonkey_id
from app.services.surveymonkey_campaign_template_service import (
    extract_surveymonkey_survey_id,
)


settings = get_settings()

if not settings.surveymonkey_access_token:
    raise RuntimeError("SURVEYMONKEY_ACCESS_TOKEN is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

created_survey_id = None

try:
    title = (
        "TEST - DELETE ME - Message Draft Check - "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}"
    )
    survey = client.create_survey(title=title)
    created_survey_id = extract_surveymonkey_survey_id(survey)

    collector = client.create_email_collector(
        survey_id=created_survey_id,
        name="TEST - DELETE ME - Email Collector",
    )
    collector_id = extract_surveymonkey_id(collector, "collector")

    try:
        message = client.create_collector_message(
            collector_id=collector_id,
            subject="TEST - DELETE ME - Survey invitation",
            body=(
                "This is a draft-only API validation. "
                "No recipient should receive this. [SurveyLink]\n\n"
                "Privacy: [PrivacyLink]\n\n"
                "Unsubscribe: [OptOutLink]\n\n"
                "Footer: [FooterLink]"
            ),
        )
    except HTTPError as exc:
        print(f"message_create_status: {exc.response.status_code}")
        print(f"message_create_error: {exc.response.text}")
        raise
    message_id = extract_surveymonkey_id(message, "message")

    print("SurveyMonkey message draft creation worked")
    print(f"created_survey_id: {created_survey_id}")
    print(f"collector_id: {collector_id}")
    print(f"message_id: {message_id}")
    print("sent_email: false")
finally:
    if created_survey_id:
        client.delete_survey(created_survey_id)
        print("deleted_test_survey: true")
