"""List Zoho Desk departments for the configured org.

Use the printed department id as ZOHO_DEPARTMENT_ID in .env.
"""

from app.core.config import get_settings
from app.integrations.zoho_desk_client import build_zoho_desk_client


settings = get_settings()

if not settings.zoho_org_id:
    raise RuntimeError("ZOHO_ORG_ID is not configured (run check_zoho_auth.py first)")

client = build_zoho_desk_client(settings)

departments = client.list_departments()

for department in departments.get("data", []):
    print(
        f"{department.get('id')} - "
        f"{department.get('name')} - "
        f"enabled: {department.get('isEnabled')}"
    )
