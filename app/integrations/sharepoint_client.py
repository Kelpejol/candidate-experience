"""Thin Microsoft Graph client for reading the Helpdesk KB from SharePoint.

Uses the client-credentials (app-only) flow via MSAL — no signed-in user,
matching the same "unattended service identity" shape as the Zoho and
inference-gateway integrations. Requires the Microsoft Graph `Sites.Selected`
application permission, admin-consented, with the app explicitly granted
access to the one SharePoint site it needs (see docs/blocked-items-and-
helpdesk-plan.md for the exact IT steps).
"""

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class SharePointClient:
    """Authenticated wrapper around the Microsoft Graph endpoints the KB
    connector needs: resolving the site, then listing/reading its files."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )

    def _get_access_token(self) -> str:
        """Acquire an app-only Graph token, using MSAL's in-memory cache.

        Raises RuntimeError with Graph/AAD's own error description on
        failure — these are informative (e.g. AADSTS7000215 for a bad
        secret, AADSTS700016 for a wrong client/tenant id).
        """
        result = self._app.acquire_token_silent(
            scopes=["https://graph.microsoft.com/.default"], account=None
        )
        if not result:
            result = self._app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        if "access_token" not in result:
            raise RuntimeError(
                f"Azure AD token acquisition failed: "
                f"{result.get('error')}: {result.get('error_description')}"
            )
        return result["access_token"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token()}"}

    def get_site(self, hostname: str, site_path: str) -> dict:
        """Resolve a SharePoint site to its Graph site id.

        This is the first real permission checkpoint: a 401 means the
        token/app registration itself is wrong (tenant/client/secret); a 403
        means auth is fine but the app hasn't been granted access to this
        specific site yet (the Sites.Selected per-site grant step).
        """
        response = requests.get(
            f"{GRAPH_BASE_URL}/sites/{hostname}:{site_path}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_drive_items(self, site_id: str, path: str = "") -> dict:
        """List files in a site's default document library, optionally under
        a sub-path (e.g. 'Policy documents')."""
        suffix = f":/{path}:" if path else ""
        response = requests.get(
            f"{GRAPH_BASE_URL}/sites/{site_id}/drive/root{suffix}/children",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def download_file(self, site_id: str, item_id: str) -> bytes:
        """Download a drive item's raw content by its Graph item id."""
        response = requests.get(
            f"{GRAPH_BASE_URL}/sites/{site_id}/drive/items/{item_id}/content",
            headers=self._headers(),
            timeout=60,
        )
        response.raise_for_status()
        return response.content


def build_sharepoint_client(settings) -> SharePointClient:
    """Build a SharePointClient from app `Settings`.

    Raises RuntimeError naming every missing credential, so the diagnostic
    script fails with an actionable message instead of an opaque MSAL error.
    """
    required = {
        "KB_READER_TENANT_ID": settings.kb_reader_tenant_id,
        "KB_READER_CLIENT_ID": settings.kb_reader_client_id,
        "KB_READER_SECRET_VALUE": settings.kb_reader_secret_value,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SharePoint reader settings: {', '.join(missing)}")

    return SharePointClient(
        tenant_id=settings.kb_reader_tenant_id,
        client_id=settings.kb_reader_client_id,
        client_secret=settings.kb_reader_secret_value,
    )
