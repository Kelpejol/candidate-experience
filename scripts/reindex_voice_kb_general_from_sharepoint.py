"""Reindex the voice KB's general scope (universal content, every campaign)
from a SharePoint folder of .docx files.

General isn't a per-campaign or per-tool concept, so unlike the campaign-admin
API route (which needs a real Campaign row) or the tool scripts (keyed by a
ALLOWED_TOOLS name), this just reindexes the one reserved GENERAL_SCOPE tag
directly — same shared pipeline underneath.

Run: PYTHONPATH=. .venv/bin/python scripts/reindex_voice_kb_general_from_sharepoint.py "KB/General"
"""

import sys

from app.core.config import get_settings
from app.integrations.sharepoint_client import build_sharepoint_client
from app.services.sharepoint_kb_loader import load_sharepoint_kb_folder
from app.services.voice_kb_ingest import reindex_campaign
from app.services.voice_kb_store import GENERAL_SCOPE

if len(sys.argv) != 2:
    raise SystemExit('Usage: python scripts/reindex_voice_kb_general_from_sharepoint.py "<SharePoint folder>"')

folder_path = sys.argv[1]
settings = get_settings()
client = build_sharepoint_client(settings)
documents, warnings = load_sharepoint_kb_folder(
    client,
    hostname=settings.sharepoint_hostname,
    site_path=settings.sharepoint_site_path,
    folder_path=folder_path,
    library_name=settings.sharepoint_library_name,
)

if warnings:
    print("Warnings:")
    for warning in warnings:
        print(f"  - {warning}")
    print()

if not documents:
    raise SystemExit(f"No usable .docx content found in SharePoint folder {folder_path!r}.")

count = reindex_campaign(GENERAL_SCOPE, documents)
print(f"Indexed {count} chunks into general scope (voice KB)")
