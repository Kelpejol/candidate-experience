"""Rebuild the Helpdesk KB index from a SharePoint folder of .docx files.

Companion to sync_helpdesk_kb.py (which rebuilds from the local kb/ folder) —
this pulls real content instead, via the same SharePoint pipeline the voice
KB uses (sharepoint_kb_loader -> docx_kb_loader), so both KBs are authored
under the identical standard: one heading (Word's "Heading 1" or "Heading 2"
style) per question.

Run: PYTHONPATH=. .venv/bin/python scripts/sync_helpdesk_kb_from_sharepoint.py <folder>
e.g.: ... sync_helpdesk_kb_from_sharepoint.py "Helpdesk FAQ"
"""

import sys

from app.services.helpdesk_kb_service import rebuild_kb_index_from_sharepoint

if len(sys.argv) != 2:
    raise SystemExit(
        'Usage: python scripts/sync_helpdesk_kb_from_sharepoint.py "<SharePoint folder>"'
    )

folder_path = sys.argv[1]
result = rebuild_kb_index_from_sharepoint(folder_path)

if result["warnings"]:
    print("Warnings:")
    for warning in result["warnings"]:
        print(f"  - {warning}")
    print()

print(f"Indexed {result['chunks_indexed']} chunks into ChromaDB")
