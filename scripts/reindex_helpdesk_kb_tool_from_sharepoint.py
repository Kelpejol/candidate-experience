"""Reindex a tool-scoped Helpdesk KB (FOT / Test Haven / Scholastica) from a
SharePoint folder of .docx files.

Same shared pipeline as the general Helpdesk KB SharePoint sync
(sharepoint_kb_loader.load_sharepoint_kb_folder -> docx_kb_loader.
docx_to_markdown), just keyed by the tool's own scope tag instead of the
default general scope, mirroring the voice KB's tool-scoped reindex script.

Run: PYTHONPATH=. .venv/bin/python scripts/reindex_helpdesk_kb_tool_from_sharepoint.py "Test Haven" "KB/Test Haven"
"""

import sys

from app.core.vocabulary import ALLOWED_TOOLS, is_tool_allowed
from app.services.campaign_scope_service import tool_name_to_scope
from app.services.helpdesk_kb_service import rebuild_kb_index_from_sharepoint

if len(sys.argv) != 3:
    raise SystemExit(
        'Usage: python scripts/reindex_helpdesk_kb_tool_from_sharepoint.py '
        '"<tool name>" "<SharePoint folder>"\n'
        f"Allowed tool names: {sorted(ALLOWED_TOOLS)}"
    )

tool_name, folder_path = sys.argv[1], sys.argv[2]
if not is_tool_allowed(tool_name):
    raise SystemExit(f"Unknown tool {tool_name!r}. Allowed: {sorted(ALLOWED_TOOLS)}")

scope = tool_name_to_scope(tool_name)
result = rebuild_kb_index_from_sharepoint(folder_path, scope=scope)

if result["warnings"]:
    print("Warnings:")
    for warning in result["warnings"]:
        print(f"  - {warning}")
    print()

print(f"Indexed {result['chunks_indexed']} chunks into tool scope {scope!r} (from {tool_name!r})")
