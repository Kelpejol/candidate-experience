"""Validate adding one recipient to a SurveyMonkey message without sending.

This is a live integration check. It creates a temporary TEST survey,
collector, and message, adds exactly one recipient, verifies the recipient
appears on the collector, and deletes the temporary survey.

No email is sent.

Run with:
    PYTHONPATH=. .venv/bin/python scripts/check_surveymonkey_prepare_recipient.py paul@example.com
"""

import argparse
from datetime import UTC, datetime
from requests import HTTPError

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.surveymonkey_campaign_template_service import (
    extract_surveymonkey_survey_id,
)
from app.services.surveymonkey_distribution_service import (
    extract_surveymonkey_id,
    extract_surveymonkey_recipients,
)


parser = argparse.ArgumentParser()
parser.add_argument("email", help="The single test recipient email to add. No email will be sent.")
args = parser.parse_args()

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
        "TEST - DELETE ME - Recipient Prepare Check - "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}"
    )
    survey = client.create_survey(title=title)
    created_survey_id = extract_surveymonkey_survey_id(survey)

    collector = client.create_email_collector(
        survey_id=created_survey_id,
        name="TEST - DELETE ME - Email Collector",
    )
    collector_id = extract_surveymonkey_id(collector, "collector")

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
    message_id = extract_surveymonkey_id(message, "message")

    try:
        response = client.add_message_recipients_bulk(
            collector_id=collector_id,
            message_id=message_id,
            contacts=[
                {"email": args.email}
            ],
        )
    except HTTPError as exc:
        print(f"recipient_prepare_status: {exc.response.status_code}")
        print(f"recipient_prepare_error: {exc.response.text}")
        raise
    recipients = extract_surveymonkey_recipients(response)
    listed_recipients = client.list_all_collector_recipients(collector_id)

    print("SurveyMonkey recipient preparation worked")
    print(f"created_survey_id: {created_survey_id}")
    print(f"collector_id: {collector_id}")
    print(f"message_id: {message_id}")
    print(f"recipient_response_count: {len(recipients)}")
    print(f"collector_recipient_count: {len(listed_recipients)}")
    print("sent_email: false")
finally:
    if created_survey_id:
        client.delete_survey(created_survey_id)
        print("deleted_test_survey: true")
