"""Verify Zoho OAuth credentials and discover the org id.

Refreshes an access token, then lists the Desk organizations the token can
reach. Use the printed org id as ZOHO_ORG_ID in .env.
"""

from app.core.config import get_settings
from app.integrations.zoho_desk_client import build_zoho_desk_client


settings = get_settings()

client = build_zoho_desk_client(settings)

access_token = client.token_provider.get_access_token()
print(f"Access token OK: {access_token[:8]}...{access_token[-4:]}")

organizations = client.list_organizations()

for organization in organizations.get("data", []):
    print(
        f"{organization.get('id')} - "
        f"{organization.get('companyName')} - "
        f"portal: {organization.get('portalName')}"
    )
