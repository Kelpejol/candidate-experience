"""Small REST client for Azure Document Intelligence Read OCR.

Uses the v4 `prebuilt-read` model so screenshots, PDFs, and scanned
documents can become text for the Helpdesk AI context. Kept as a thin
requests-based client to match the rest of this codebase's integration style.
"""

import time

import requests


class AzureDocumentIntelligenceError(RuntimeError):
    """Raised when Azure Document Intelligence cannot OCR a document."""


class AzureDocumentIntelligenceClient:
    def __init__(
        self,
        endpoint: str,
        key: str,
        api_version: str = "2024-11-30",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.key = key
        self.api_version = api_version

    def read_bytes(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        timeout_seconds: int = 60,
        poll_interval_seconds: float = 1.0,
    ) -> str:
        """Return extracted text from a file's bytes, or raise on failure."""
        analyze_url = (
            f"{self.endpoint}/documentintelligence/documentModels/"
            f"prebuilt-read:analyze"
        )
        response = requests.post(
            analyze_url,
            params={"api-version": self.api_version},
            headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": content_type,
            },
            content=data,
            timeout=30,
        )
        response.raise_for_status()
        operation_url = (
            response.headers.get("operation-location")
            or response.headers.get("Operation-Location")
            or response.headers.get("Location")
        )
        if not operation_url:
            raise AzureDocumentIntelligenceError("Azure OCR response had no operation URL")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            result_response = requests.get(
                operation_url,
                headers={"Ocp-Apim-Subscription-Key": self.key},
                timeout=30,
            )
            result_response.raise_for_status()
            payload = result_response.json()
            status = (payload.get("status") or "").lower()

            if status == "succeeded":
                return (payload.get("analyzeResult") or {}).get("content") or ""
            if status == "failed":
                raise AzureDocumentIntelligenceError("Azure OCR operation failed")

            time.sleep(poll_interval_seconds)

        raise AzureDocumentIntelligenceError("Azure OCR operation timed out")


def build_azure_document_intelligence_client(settings):
    if not settings.azure_document_intelligence_endpoint:
        raise RuntimeError("Missing AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    if not settings.azure_document_intelligence_key:
        raise RuntimeError("Missing AZURE_DOCUMENT_INTELLIGENCE_KEY")
    return AzureDocumentIntelligenceClient(
        endpoint=settings.azure_document_intelligence_endpoint,
        key=settings.azure_document_intelligence_key,
        api_version=settings.azure_document_intelligence_api_version,
    )
