from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient




settings = get_settings()

if not settings.surveymonkey_access_token:
    raise RuntimeError("SURVEYMONKEY_ACCESS_TOKEN is not configured")


client = SurveyMonkeyClient(
    base_url = settings.surveymonkey_base_url,
    access_token = settings.surveymonkey_access_token
)

surveys = client.list_surveys()

for survey in surveys.get("data", []):
    print(f"{survey.get('id')} - {survey.get('title')}")