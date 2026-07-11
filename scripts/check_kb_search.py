"""Query the Helpdesk KB index and show the top matches with distances.

Usage: python scripts/check_kb_search.py "candidate question here"

Cosine DISTANCE: 0 = identical meaning, higher = less related. The gap
between real-question distances and nonsense-question distances is where
the grounded/not-grounded threshold gets calibrated.
"""

import sys

from app.services.helpdesk_kb_service import embed_text, get_kb_collection

if len(sys.argv) != 2:
    raise SystemExit('Usage: python scripts/check_kb_search.py "question"')

query = sys.argv[1]
collection = get_kb_collection()
result = collection.query(query_embeddings=[embed_text(query)], n_results=3)

print(f'Query: "{query}"\n')
for distance, meta, doc in zip(
    result["distances"][0], result["metadatas"][0], result["documents"][0]
):
    body_preview = " ".join(doc.split())[:110]
    print(f"[distance={distance:.3f}] {meta['source']} :: {meta['heading']}")
    print(f"   {body_preview}...\n")
