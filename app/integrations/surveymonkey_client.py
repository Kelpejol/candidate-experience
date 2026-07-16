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

    def _post(self, path: str, payload: dict | None = None) -> dict:
        """Issue an authenticated POST to `path` and return the parsed JSON body."""
        response = requests.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload or {},
            timeout=30,
        )
        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()

    def _patch(self, path: str, payload: dict | None = None) -> dict:
        """Issue an authenticated PATCH to `path` and return the parsed JSON body."""
        response = requests.patch(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload or {},
            timeout=30,
        )
        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()

    def _delete(self, path: str) -> dict:
        """Issue an authenticated DELETE to `path` and return the parsed JSON body."""
        response = requests.delete(
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

        if not response.content:
            return {}

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

    def list_all_surveys(self) -> list[dict]:
        """Fetch every survey accessible to the authenticated account."""
        return self._get_all_pages("/surveys")


    def list_collectors(self, survey_id: str) -> dict:
        """Fetch the first page of collectors (distribution channels) for a survey."""
        response = requests.get(
            f"{self.base_url}/surveys/{survey_id}/collectors",
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    def create_email_collector(self, survey_id: str, name: str) -> dict:
        """Create an email collector for a survey."""
        return self._post(
            f"/surveys/{survey_id}/collectors",
            payload={
                "type": "email",
                "name": name,
            },
        )

    def create_collector_message(
        self,
        collector_id: str,
        subject: str,
        body: str | None = None,
        type_: str = "invite",
    ) -> dict:
        """Create an email invitation message under a collector."""
        message = self._post(
            f"/collectors/{collector_id}/messages",
            payload={
                "type": type_,
            },
        )
        message_id = str(message["id"])
        payload = {"subject": subject}

        if body:
            payload["body_text"] = body

        return self._patch(
            f"/collectors/{collector_id}/messages/{message_id}",
            payload=payload,
        )

    def add_message_recipients_bulk(
        self,
        collector_id: str,
        message_id: str,
        contacts: list[dict],
    ) -> dict:
        """Add recipients to a collector message without sending the message."""
        return self._post(
            f"/collectors/{collector_id}/messages/{message_id}/recipients/bulk",
            payload={"contacts": contacts},
        )

    def send_collector_message(self, collector_id: str, message_id: str) -> dict:
        """Send a prepared collector message to its recipients."""
        return self._post(
            f"/collectors/{collector_id}/messages/{message_id}/send",
        )


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

    def create_survey(self, title: str, category: str | None = None) -> dict:
        """Create a new draft survey."""
        payload = {"title": title}

        if category:
            payload["category"] = category

        return self._post("/surveys", payload=payload)

    def update_page(self, survey_id: str, page_id: str, payload: dict) -> dict:
        """Update a page on a draft survey."""
        return self._patch(
            f"/surveys/{survey_id}/pages/{page_id}",
            payload=payload,
        )

    def create_page(self, survey_id: str, payload: dict) -> dict:
        """Create a page on a draft survey."""
        return self._post(
            f"/surveys/{survey_id}/pages",
            payload=payload,
        )

    def create_question(self, survey_id: str, page_id: str, payload: dict) -> dict:
        """Create a question on a draft survey page."""
        return self._post(
            f"/surveys/{survey_id}/pages/{page_id}/questions",
            payload=payload,
        )

    def delete_survey(self, survey_id: str) -> dict:
        """Delete a survey."""
        return self._delete(f"/surveys/{survey_id}")


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
