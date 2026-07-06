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
