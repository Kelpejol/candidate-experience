"""Reindex a tool-scoped voice KB (FOT / Test Haven / Scholastica) from a
SharePoint folder of .docx files.

Same shared pipeline as the campaign KB's SharePoint reindex
(sharepoint_kb_loader.load_sharepoint_kb_folder -> docx_kb_loader.
docx_to_markdown) — just keyed by the tool's own scope tag instead of a
campaign id, since a tool isn't a Campaign row.

Run: PYTHONPATH=. .venv/bin/python scripts/reindex_voice_kb_tool_from_sharepoint.py "Test Haven" "Test Haven FAQ"
"""

import sys

from app.core.config import get_settings
from app.core.vocabulary import ALLOWED_TOOLS, is_tool_allowed
from app.integrations.sharepoint_client import build_sharepoint_client
from app.services.campaign_scope_service import tool_name_to_scope
from app.services.sharepoint_kb_loader import load_sharepoint_kb_folder
from app.services.voice_kb_ingest import reindex_campaign

if len(sys.argv) != 3:
    raise SystemExit(
        'Usage: python scripts/reindex_voice_kb_tool_from_sharepoint.py '
        '"<tool name>" "<SharePoint folder>"\n'
        f"Allowed tool names: {sorted(ALLOWED_TOOLS)}"
    )

tool_name, folder_path = sys.argv[1], sys.argv[2]
if not is_tool_allowed(tool_name):
    raise SystemExit(f"Unknown tool {tool_name!r}. Allowed: {sorted(ALLOWED_TOOLS)}")

scope = tool_name_to_scope(tool_name)
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

count = reindex_campaign(scope, documents)
print(f"Indexed {count} chunks into tool scope {scope!r} (from {tool_name!r})")
