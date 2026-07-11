"""Exchange a Zoho Self Client grant code for a refresh token.

Grant codes from the API console's "Generate Code" tab are single-use and
expire within minutes. This script exchanges one for a permanent refresh
token, which belongs in ZOHO_REFRESH_TOKEN in .env.

Usage:
    python scripts/exchange_zoho_grant_code.py <grant_code>
"""

import sys

import requests

from app.core.config import get_settings


settings = get_settings()

if len(sys.argv) != 2:
    raise RuntimeError("Usage: python scripts/exchange_zoho_grant_code.py <grant_code>")

if not settings.zoho_client_id or not settings.zoho_client_secret:
    raise RuntimeError("ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET are not configured")

grant_code = sys.argv[1]

response = requests.post(
    f"{settings.zoho_accounts_base_url.rstrip('/')}/oauth/v2/token",
    params={
        "grant_type": "authorization_code",
        "code": grant_code,
        "client_id": settings.zoho_client_id,
        "client_secret": settings.zoho_client_secret,
    },
    timeout=30,
)
response.raise_for_status()
payload = response.json()

if "error" in payload:
    raise RuntimeError(
        f"Zoho rejected the grant code: {payload['error']} "
        "(codes are single-use and expire in minutes — generate a fresh one)"
    )

refresh_token = payload.get("refresh_token")
if not refresh_token:
    raise RuntimeError(f"No refresh_token in response: {payload}")

print("Add this to .env:")
print(f"ZOHO_REFRESH_TOKEN={refresh_token}")
