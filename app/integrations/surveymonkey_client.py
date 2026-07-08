"""Thin HTTP client for the SurveyMonkey REST API.

Wraps the endpoints needed to sync candidate survey (collector recipients)
and response data for a campaign: listing surveys/collectors, fetching
survey details, and paging through collector recipients and bulk survey
responses.
"""

import requests

class SurveyMonkeyClient:
    """Authenticated wrapper around a subset of the SurveyMonkey v3 REST API."""

    def __init__(self, base_url: str, access_token: str):
        """Store the API base URL and OAuth access token used for every request."""
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        """Build the bearer-auth headers required by every SurveyMonkey API call."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Issue an authenticated GET to `path` and return the parsed JSON body.

        Side effects: makes an HTTP request to the SurveyMonkey API. Raises
        requests.HTTPError via raise_for_status() on non-2xx responses.
        """
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    def _get_all_pages(self, path: str) -> list[dict]:
        """Page through a SurveyMonkey list endpoint and return all items.

        SurveyMonkey paginates with `page`/`per_page` query params and
        reports both a `total` item count and a `links.next` URL; since we
        only use `links.next` as a presence check (not the URL itself), we
        stop once we've collected `total` items or the API stops reporting
        a next page, whichever comes first.
        """
        page = 1
        all_items = []

        while True:
            payload = self._get(
                path,
                params={
                    "page": page,
                    "per_page": 100
                },
            )

            items = payload.get("data", [])
            all_items.extend(items)

            total = payload.get("total")
            next_link = payload.get("links", {}).get("next")

            if total is not None and len(all_items) >= total:
                break

            if not next_link:
                break

            page += 1

        return all_items

    def list_surveys(self) -> dict:
        """Fetch the first page of surveys accessible to the authenticated account."""
        response = requests.get(
            f"{self.base_url}/surveys",
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()


    def list_collectors(self, survey_id: str) -> dict:
        """Fetch the first page of collectors (distribution channels) for a survey."""
        response = requests.get(
            f"{self.base_url}/surveys/{survey_id}/collectors",
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()


    def list_collector_recipients(self, collector_id: str) -> dict:
        """Fetch the first page of recipients (invited candidates) for a collector."""
        response = requests.get(
            f"{self.base_url}/collectors/{collector_id}/recipients",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


    def get_survey_details(self, survey_id: str) -> dict:
        """Fetch the full survey definition (pages/questions) for a survey."""
        return self._get(f"/surveys/{survey_id}/details")


    def list_survey_responses_bulk(self, survey_id: str) -> dict:
        """Fetch the first page of bulk (full-detail) responses for a survey."""
        response = requests.get(
            f"{self.base_url}/surveys/{survey_id}/responses/bulk",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()



    def list_all_collector_recipients(self, collector_id: str) -> list[dict]:
        """Fetch every recipient for a collector, following pagination."""
        return self._get_all_pages(
            f"/collectors/{collector_id}/recipients"
        )

    def list_all_survey_responses_bulk(self, survey_id: str) -> list[dict]:
        """Fetch every bulk survey response for a survey, following pagination."""
        return self._get_all_pages(
            f"/surveys/{survey_id}/responses/bulk"
        )