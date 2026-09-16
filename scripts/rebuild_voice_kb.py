"""Rebuild the Calling Agent's voice KB from kb_voice/*.md (clean full re-index).

Drops the voice_kb collection and re-indexes every markdown file in kb_voice/,
embedding each heading section through the inference gateway. Run this whenever
the kb_voice content changes.

Run with: PYTHONPATH=. .venv/bin/python scripts/rebuild_voice_kb.py
"""

from pathlib import Path

from app.services.voice_kb_ingest import index_document
from app.services.voice_kb_store import GENERAL_SCOPE, delete_by_campaign

KB_DIR = Path("kb_voice")

# Clear only the 'general' scope, so rebuilding the universal KB never wipes
# any campaign-specific KBs that also live in the collection.
delete_by_campaign(GENERAL_SCOPE)
print(f"cleared '{GENERAL_SCOPE}' scope. Indexing kb_voice/ as general ...\n")

total = 0
for path in sorted(KB_DIR.glob("*.md")):
    count = index_document(path.read_text(), source=path.name)  # campaign None -> general
    print(f"  {path.name}: {count} chunks")
    total += count

print(f"\nDone. {total} chunks indexed into voice_kb.")
