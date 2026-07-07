from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.surveymonkey_sync_service import summarize_collector_responses


settings = get_settings()

if not settings.surveymonkey_campaign_survey_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_SURVEY_ID is not configured")

if not settings.surveymonkey_campaign_collector_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_COLLECTOR_ID is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

recipients = client.list_all_collector_recipients(
    settings.surveymonkey_campaign_collector_id
)

responses = client.list_all_survey_responses_bulk(
    settings.surveymonkey_campaign_survey_id
)

summary = summarize_collector_responses(
    recipients=recipients,
    responses=responses,
    collector_id=settings.surveymonkey_campaign_collector_id,
)

non_responders = summary["non_responder_recipients"]
print(f"total recipients: {summary['total_recipients']}")
print(f"completed responses: {summary['completed']}")
print(f"partial responses: {summary['partial']}")
print(f"non responders: {summary['non_responders']}")
print()

for recipient in non_responders[:10]:
    print(f"{recipient.get('id')} - {recipient.get('email')}")