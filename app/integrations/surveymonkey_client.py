import requests

class SurveyMonkeyClient:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
    
    def _get(self, path: str, params: dict | None = None) -> dict:
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def _get_all_pages(self, path: str) -> list[dict]:
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
        response = requests.get(
            f"{self.base_url}/surveys",
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()


    def list_collectors(self, survey_id: str) -> dict:
        response = requests.get(
            f"{self.base_url}/surveys/{survey_id}/collectors",
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    
    def list_collector_recipients(self, collector_id: str) -> dict:
        response = requests.get(
            f"{self.base_url}/collectors/{collector_id}/recipients",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    

    def list_survey_responses_bulk(self, survey_id: str) -> dict:
        response = requests.get(
            f"{self.base_url}/surveys/{survey_id}/responses/bulk",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    


    def list_all_collector_recipients(self, collector_id: str) -> list[dict]:
        return self._get_all_pages(
            f"/collectors/{collector_id}/recipients"
        )

    def list_all_survey_responses_bulk(self, survey_id: str) -> list[dict]:
        return self._get_all_pages(
            f"/surveys/{survey_id}/responses/bulk"
        )