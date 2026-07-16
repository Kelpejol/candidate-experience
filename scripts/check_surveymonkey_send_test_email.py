"""Send one real SurveyMonkey test invitation email.

This script creates a TEST survey from the configured campaign template,
creates an email collector/message, adds exactly one recipient, and sends
the message. The created survey is kept so the email link remains usable.

If the flow fails before sending, the created TEST survey is deleted.

Run with:
    PYTHONPATH=. .venv/bin/python scripts/check_surveymonkey_send_test_email.py paul@dragnet-solutions.com
"""

import argparse
from datetime import UTC, datetime

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.surveymonkey_campaign_template_service import (
    clone_surveymonkey_survey_from_template,
)
from app.services.surveymonkey_distribution_service import (
    extract_surveymonkey_id,
    extract_surveymonkey_recipients,
)


parser = argparse.ArgumentParser()
parser.add_argument("email", help="The single test recipient email to send to.")
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

created_survey_id = None
sent = False

try:
    title = (
        "TEST - DELETE ME - Candidate Experience Send Check - "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}"
    )
    created_survey_id = clone_surveymonkey_survey_from_template(
        client=client,
        template_survey_id=settings.surveymonkey_campaign_template_survey_id,
        title=title,
        variables={
            "campaign_name": "Candidate Experience Send Check",
            "campaign_title": "Candidate Experience Send Check",
            "assessment_name": "Candidate Experience Send Check",
            "tool_name": "FOT",
        },
    )

    collector = client.create_email_collector(
        survey_id=created_survey_id,
        name="TEST - DELETE ME - Email Collector",
    )
    collector_id = extract_surveymonkey_id(collector, "collector")

    message = client.create_collector_message(
        collector_id=collector_id,
        subject="TEST - Candidate Experience Survey Invitation",
        body=None,
    )
    message_id = extract_surveymonkey_id(message, "message")

    recipient_response = client.add_message_recipients_bulk(
        collector_id=collector_id,
        message_id=message_id,
        contacts=[{"email": args.email}],
    )
    recipients = extract_surveymonkey_recipients(recipient_response)

    if not recipients:
        raise RuntimeError("SurveyMonkey did not return a prepared recipient")

    send_response = client.send_collector_message(
        collector_id=collector_id,
        message_id=message_id,
    )
    sent = True

    print("SurveyMonkey test email sent")
    print(f"recipient_email: {args.email}")
    print(f"created_survey_id: {created_survey_id}")
    print(f"collector_id: {collector_id}")
    print(f"message_id: {message_id}")
    print(f"recipient_count: {len(recipients)}")
    print("message_body: SurveyMonkey default invite body")
    print(f"send_response: {send_response}")
    print("kept_test_survey: true")
except Exception:
    if created_survey_id and not sent:
        client.delete_survey(created_survey_id)
        print("deleted_test_survey_after_failed_send: true")

    raise
