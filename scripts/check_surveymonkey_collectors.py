import sys

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient


settings = get_settings()

if len(sys.argv) != 2:
    raise RuntimeError("Usage: python scripts/check_surveymonkey_collectors.py <survey_id>")

if not settings.surveymonkey_access_token:
    raise RuntimeError("SURVEYMONKEY_ACCESS_TOKEN is not configured")

survey_id = sys.argv[1]

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

collectors = client.list_collectors(survey_id)

for collector in collectors.get("data", []):
    print(
        f"{collector.get('id')} - "
        f"{collector.get('name')} - "
        f"{collector.get('type')} - "
        f"{collector.get('status')}"
    )