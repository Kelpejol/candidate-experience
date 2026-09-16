"""Diagnose the SharePoint KB connector permission chain, step by step.

Tells you exactly which layer is missing rather than one opaque failure:
1. Can we get an Azure AD token at all? (app registration itself is valid)
2. Can we resolve the SharePoint site? (Sites.Selected admin consent +
   the per-site access grant)
3. Can we list files in it? (drive/library access, same grant covers this)

Run with: python scripts/check_sharepoint_access.py
"""

from app.core.config import get_settings
from app.integrations.sharepoint_client import build_sharepoint_client

settings = get_settings()

print("Step 1: acquiring an Azure AD app-only token...")
client = build_sharepoint_client(settings)
try:
    token = client._get_access_token()
    print(f"  OK — token acquired ({token[:12]}...)\n")
except RuntimeError as exc:
    print(f"  FAILED: {exc}")
    print("  -> app registration itself is wrong: check tenant/client id or secret value/expiry.")
    raise SystemExit(1)

print(f"Step 2: resolving site {settings.sharepoint_hostname}{settings.sharepoint_site_path} ...")
try:
    site = client.get_site(settings.sharepoint_hostname, settings.sharepoint_site_path)
    site_id = site["id"]
    print(f"  OK — site id: {site_id}")
    print(f"  name: {site.get('displayName')}\n")
except Exception as exc:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    print(f"  FAILED ({status}): {exc}")
    if status == 403:
        print("  -> token is valid, but this app has not been granted access to this")
        print("     specific site yet. That's the Sites.Selected per-site grant step —")
        print("     still needed from IT/a SharePoint admin.")
    elif status == 401:
        print("  -> Sites.Selected permission may not have admin consent yet.")
    raise SystemExit(1)

drive_id = None
if settings.sharepoint_library_name:
    print(f"Step 3: resolving library {settings.sharepoint_library_name!r} on this site...")
    try:
        drive_id = client.get_drive_id(site_id, settings.sharepoint_library_name)
        print(f"  OK — drive id: {drive_id}\n")
    except RuntimeError as exc:
        print(f"  FAILED: {exc}")
        raise SystemExit(1)
    step = "4"
else:
    step = "3"

print(f"Step {step}: listing files in the {'named' if drive_id else 'default'} document library...")
items = client.list_drive_items(site_id, drive_id=drive_id).get("value", [])
for item in items:
    kind = "folder" if "folder" in item else "file"
    print(f"  [{kind}] {item.get('name')}  (id={item.get('id')})")

print(f"\nAll checks passed — {len(items)} item(s) at the library root.")
