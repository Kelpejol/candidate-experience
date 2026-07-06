from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient


settings = get_settings()

if not settings.surveymonkey_campaign_collector_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_COLLECTOR_ID is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

recipients = client.list_collector_recipients(
    settings.surveymonkey_campaign_collector_id
)

for recipient in recipients.get("data", []):
    print(
        f"{recipient.get('id')} - "
        f"{recipient.get('email')} - "
        f"{recipient.get('first_name')} "
        f"{recipient.get('last_name')} - "
        f"{recipient.get('status')}"
    )