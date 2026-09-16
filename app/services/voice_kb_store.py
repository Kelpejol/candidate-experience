"""Vector-store seam for the Calling Agent knowledge base.

A thin interface over the vector store. The rest of the KB pipeline talks to
THIS module, never to Chroma directly — so switching to pgvector at go-live is
a change to this one file. "Bring your own embeddings": callers pass vectors
(produced by the inference gateway); this module only stores and searches them.

Separate from the Helpdesk KB by design — its own collection.
"""

from chromadb import PersistentClient

from app.core.config import get_settings

COLLECTION_NAME = "voice_kb"

# Reserved scope for universal content that applies to every campaign
# (e.g. "who is Dragnet", fees, browser help). A scoped query always includes
# it alongside the specific campaign's chunks.
GENERAL_SCOPE = "general"


def _collection():
    """Open (or create) our cosine-distance collection in the Chroma store."""
    settings = get_settings()
    client = PersistentClient(path=settings.chroma_dir)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection():
    """Drop and recreate the collection — used for a clean full re-index."""
    settings = get_settings()
    client = PersistentClient(path=settings.chroma_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(ids, embeddings, documents, metadatas):
    """Insert or replace chunks. The four lists are parallel (same order)."""
    _collection().upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


def query(embedding, k=3, campaign=None, extra_scopes=None):
    """Return the k nearest chunks to a query embedding, scoped by campaign.

    - campaign given  -> that campaign's chunks PLUS shared 'general' chunks
      PLUS any extra_scopes (e.g. the campaign's assessment-tool scope —
      see campaign_scope_service.campaign_tool_scope — so a candidate's
      question can match general OR tool-specific OR campaign-specific
      content in one query).
    - campaign None    -> only shared 'general' chunks (extra_scopes is
      ignored — there's no campaign to derive a tool scope from).

    Returns our own shape ({id, document, distance, metadata}); empty list when
    nothing matches the scope.
    """
    scopes = [GENERAL_SCOPE]
    if campaign and campaign != GENERAL_SCOPE:
        scopes.append(campaign)
        scopes.extend(extra_scopes or [])

    if len(scopes) > 1:
        where = {"campaign": {"$in": scopes}}
    else:
        where = {"campaign": GENERAL_SCOPE}

    result = _collection().query(
        query_embeddings=[embedding], n_results=k, where=where
    )
    # Chroma returns one list-of-lists per query; guard against empties.
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    return [
        {"id": id_, "document": doc, "distance": dist, "metadata": meta}
        for id_, doc, dist, meta in zip(ids, docs, dists, metas)
    ]


def delete_by_campaign(campaign, keep_ids=None):
    """Remove a campaign's chunks, so edited/removed content never lingers.

    With `keep_ids`, only the chunks NOT in that set are removed — this is how
    a re-index sweeps stale content *after* successfully writing the new set,
    rather than deleting first and risking an empty KB if the write fails.
    """
    collection = _collection()
    if keep_ids is None:
        collection.delete(where={"campaign": campaign})
        return

    existing = collection.get(where={"campaign": campaign}, include=[])
    stale = [i for i in (existing.get("ids") or []) if i not in set(keep_ids)]
    if stale:
        collection.delete(ids=stale)
