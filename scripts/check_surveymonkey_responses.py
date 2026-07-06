from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient


settings = get_settings()

if not settings.surveymonkey_campaign_survey_id:
    raise RuntimeError("SURVEYMONKEY_CAMPAIGN_SURVEY_ID is not configured")

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

responses = client.list_survey_responses_bulk(
    settings.surveymonkey_campaign_survey_id
)

for response in responses.get("data", []):
    print("response_id:", response.get("id"))
    print("collector_id:", response.get("collector_id"))
    print("recipient_id:", response.get("recipient_id"))
    print("email:", response.get("email_address"))
    print("date_modified:", response.get("date_modified"))
    print("response_status:", response.get("response_status"))
    print("---")