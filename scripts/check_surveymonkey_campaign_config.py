from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient


settings = get_settings()

if not settings.surveymonkey_campaign_survey_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_SURVEY_ID is not configured")

if not settings.surveymonkey_campaign_collector_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_COLLECTOR_ID is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

collectors = client.list_collectors(settings.surveymonkey_campaign_survey_id)

matched = None
for collector in collectors.get("data", []):
    if collector.get("id") == settings.surveymonkey_campaign_collector_id:
        matched = collector
        break

if not matched:
    raise RuntimeError("Configured collector was not found for configured survey")

print("SurveyMonkey campaign config is valid")
print(f"survey_id: {settings.surveymonkey_campaign_survey_id}")
print(f"collector_id: {matched.get('id')}")
print(f"name: {matched.get('name')}")
print(f"type: {matched.get('type')}")
print(f"status: {matched.get('status')}")