"""Zoho OAuth token helper.

Zoho APIs use short-lived access tokens (about one hour) minted from a
long-lived refresh token. `ZohoTokenProvider` exchanges the refresh token
for an access token and caches it in memory until shortly before expiry,
so callers can ask for a token on every request without spamming the
Zoho accounts server.

The accounts host is data-centre specific (accounts.zoho.com, .eu, .in,
...) and must match the data centre the Zoho Desk org lives in.
"""

import time
from threading import Lock

import requests


class ZohoAuthError(RuntimeError):
    """Raised when Zoho refuses to mint an access token."""


class ZohoTokenProvider:
    """Mints and caches Zoho OAuth access tokens from a refresh token."""

    # Refresh this many seconds before the reported expiry to avoid using
    # a token that dies mid-request.
    EXPIRY_BUFFER_SECONDS = 120

    def __init__(
        self,
        accounts_base_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        request_timeout: float = 30,
    ):
        """Store the OAuth client credentials and refresh token."""
        self.accounts_base_url = accounts_base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.request_timeout = request_timeout
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_lock = Lock()

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it if missing or stale."""
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        with self._refresh_lock:
            if self._access_token and time.time() < self._expires_at:
                return self._access_token
            return self._refresh_access_token()

    def _refresh_access_token(self) -> str:
        """Exchange the refresh token for a new access token.

        Side effects: makes an HTTP request to the Zoho accounts server and
        updates the in-memory token cache. Raises ZohoAuthError if Zoho
        returns an error payload (Zoho reports OAuth errors such as
        `invalid_code` in a 200 body, so a status check alone is not enough).
        """
        response = requests.post(
            f"{self.accounts_base_url}/oauth/v2/token",
            params={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()

        if "error" in payload:
            raise ZohoAuthError(f"Zoho token refresh failed: {payload['error']}")

        access_token = payload.get("access_token")
        if not access_token:
            raise ZohoAuthError("Zoho token refresh response had no access_token")

        expires_in = int(payload.get("expires_in", 3600))
        self._access_token = access_token
        self._expires_at = time.time() + expires_in - self.EXPIRY_BUFFER_SECONDS
        return access_token
