"""Rebuild the Helpdesk KB index from the kb/ folder.

Run after any KB content change: python scripts/sync_helpdesk_kb.py
"""

from app.services.helpdesk_kb_service import load_kb_chunks, rebuild_kb_index
from app.core.config import get_settings


chunks = load_kb_chunks(get_settings().kb_dir)
for chunk in chunks:
    print(f"  {chunk['id']}")

result = rebuild_kb_index()
print(f"\nIndexed {result['chunks_indexed']} chunks into ChromaDB")
