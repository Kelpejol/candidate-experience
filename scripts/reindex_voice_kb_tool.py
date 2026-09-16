"""Reindex a tool-scoped voice KB (FOT / Test Haven / Scholastica) from a
local directory of .md files.

A tool isn't a Campaign row, so this doesn't go through campaign_admin's
per-campaign reindex endpoints — it calls the same source-agnostic
reindex_campaign directly, just keyed by the tool's own scope tag instead of
a campaign id. Every campaign using that tool picks up this content
automatically via the third KB tier (see campaign_scope_service.
campaign_tool_scope) — nothing per-campaign to configure.

Run: PYTHONPATH=. .venv/bin/python scripts/reindex_voice_kb_tool.py "Test Haven" kb_voice_test_haven/
"""

import sys

from app.core.vocabulary import ALLOWED_TOOLS, is_tool_allowed
from app.services.campaign_scope_service import tool_name_to_scope
from app.services.voice_kb_ingest import load_local_kb_dir, reindex_campaign

if len(sys.argv) != 3:
    raise SystemExit(
        'Usage: python scripts/reindex_voice_kb_tool.py "<tool name>" <local dir>\n'
        f"Allowed tool names: {sorted(ALLOWED_TOOLS)}"
    )

tool_name, local_dir = sys.argv[1], sys.argv[2]
if not is_tool_allowed(tool_name):
    raise SystemExit(f"Unknown tool {tool_name!r}. Allowed: {sorted(ALLOWED_TOOLS)}")

scope = tool_name_to_scope(tool_name)
documents = load_local_kb_dir(local_dir)
count = reindex_campaign(scope, documents)
print(f"Indexed {count} chunks into tool scope {scope!r} (from {tool_name!r})")
