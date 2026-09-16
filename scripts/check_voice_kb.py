"""Quick check: index a real KB doc through the LIVE gateway and query it.

Proves the Calling Agent KB pipeline end-to-end against real embeddings —
chunk -> embed -> store -> retrieve. (Writes to the `voice_kb` Chroma
collection, separate from the Helpdesk KB.)

Run with: PYTHONPATH=. .venv/bin/python scripts/check_voice_kb.py
"""

from pathlib import Path

from app.services.voice_kb_ingest import embed_text, index_document
from app.services.voice_kb_store import query

DOC = "kb/reschedules.md"
QUESTION = "what is the capital of France?"

count = index_document(Path(DOC).read_text(), source="reschedules.md")
print(f"indexed {count} chunks from {DOC}\n")
print(f"question: {QUESTION!r}\n")

for hit in query(embed_text(QUESTION), k=2):
    # heading lives inside metadata (query returns {id, document, distance, metadata})
    print(f"  distance {round(hit['distance'], 3)}  ->  {hit['metadata']['heading']}")
