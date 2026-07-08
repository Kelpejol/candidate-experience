"""Inspect a SurveyMonkey survey's structure via the SurveyMonkey API.

Fetches survey details for a given survey ID and prints its pages,
questions, and answer headings to stdout. Useful for exploring a survey's
layout (e.g. to figure out question IDs/families) before wiring up sync
logic.

Run with: python scripts/check_surveymonkey_survey_details.py <survey_id>
"""

import sys

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient


settings = get_settings()

if len(sys.argv) != 2:
    raise RuntimeError("Usage: python scripts/check_surveymonkey_survey_details.py <survey_id>")

if not settings.surveymonkey_access_token:
    raise RuntimeError("SURVEYMONKEY_ACCESS_TOKEN is not configured")

survey_id = sys.argv[1]

client = SurveyMonkeyClient(
    base_url=settings.surveymonkey_base_url,
    access_token=settings.surveymonkey_access_token,
)

details = client.get_survey_details(survey_id)

print(f"survey_id: {details.get('id')}")
print(f"title: {details.get('title')}")
print()

for page in details.get("pages", []):
    print(f"PAGE: {page.get('id')} - {page.get('title')}")

    for question in page.get("questions", []):
        headings = question.get("headings", [])
        # Headings hold the actual question/answer label text; not every
        # heading entry has text, so drop the empty ones.
        heading_texts = [
            heading.get("heading")
            for heading in headings
            if heading.get("heading")
        ]

        print(f"  QUESTION: {question.get('id')} - {question.get('family')} / {question.get('subtype')}")

        for heading_text in heading_texts:
            print(f"    HEADING: {heading_text}")

    print()