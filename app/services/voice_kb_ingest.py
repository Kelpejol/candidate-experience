"""Load + chunk KB documents for the Calling Agent.

Chunking is structure-aware: our source is FAQ-style markdown where each
heading is one self-contained question/answer, so we split on headings —
one heading section per chunk. No fixed-size cutting, no overlap needed;
each chunk is already a complete answer. (When the source moves to
SharePoint later, only the *loading* changes — this chunker stays.)
"""

from pathlib import Path

import httpx

from app.core.config import get_settings
from app.services.markdown_kb_chunker import chunk_markdown, heading_of, slug
from app.services.voice_kb_store import (
    GENERAL_SCOPE,
    delete_by_campaign,
    upsert_chunks,
)

# Re-exported so existing `from app.services.voice_kb_ingest import
# chunk_markdown` call sites (tests included) keep working unchanged — the
# real implementation now lives in markdown_kb_chunker, shared with Helpdesk.
__all__ = ["chunk_markdown"]


def embed_text(text: str, timeout: float = 60) -> list[float]:
    """Turn text into a 1024-dim vector via the inference gateway.

    `timeout` defaults to 60s for batch indexing; the live in-call retrieval
    path passes a short timeout so a slow gateway fails fast into escalation
    rather than leaving the caller in silence.
    """
    settings = get_settings()
    resp = httpx.post(
        f"{settings.inference_base_url}/embed",
        headers={"Authorization": f"Bearer {settings.inference_api_key}"},
        json={"text": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def index_document(text: str, source: str, campaign: str | None = None) -> int:
    """Chunk a markdown doc, embed each chunk, store it. Returns #chunks indexed.

    `campaign` tags every chunk so retrieval can scope to it; None means the
    universal 'general' scope. The chunk id is namespaced by campaign so two
    campaigns using the same source filename can't overwrite each other.

    Embeds all chunks first and upserts once at the end, so a mid-document
    embedding failure raises before anything is written (no partial index).
    """
    scope = campaign or GENERAL_SCOPE
    chunks = chunk_markdown(text)
    ids, embeddings, documents, metadatas = [], [], [], []
    for chunk in chunks:
        heading = heading_of(chunk)
        ids.append(f"{scope}::{source}::{slug(heading)}")
        embeddings.append(embed_text(chunk))          # ← the real gateway call, per chunk
        documents.append(chunk)
        metadatas.append({"source": source, "heading": heading, "campaign": scope})
    if ids:
        upsert_chunks(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    return len(ids)


def reindex_campaign(scope: str, documents) -> int:
    """Atomically replace a campaign's KB with a fresh set of documents.

    `documents` is an iterable of (source_name, text) — source-agnostic, so the
    loader (local dir now, SharePoint later) is the only thing that changes.

    Embeds EVERYTHING first, then WRITES the new chunks, and only deletes the
    stale ones once that write has succeeded. Ordering matters: deleting first
    means any failure afterwards (a store error, or two headings that slug to
    the same id) leaves the campaign's KB permanently empty, and every caller
    silently drops to general-only answers. Returns total chunks indexed.

    Chunk ids are made unique per (source, heading, ordinal) so headings that
    normalise identically — duplicates, or non-ASCII headings that slug to ""
    — can't collide and abort the write.
    """
    ids, embeddings, docs, metas = [], [], [], []
    for source, text in documents:
        for ordinal, chunk in enumerate(chunk_markdown(text)):
            heading = heading_of(chunk)
            ids.append(f"{scope}::{source}::{ordinal}::{slug(heading)}")
            embeddings.append(embed_text(chunk))   # embed all first (fail -> raise, no mutation)
            docs.append(chunk)
            metas.append({"source": source, "heading": heading, "campaign": scope})

    # Write first, then drop whatever the rebuild didn't produce. A failure in
    # the write leaves the previous KB serving callers untouched.
    if ids:
        upsert_chunks(ids=ids, embeddings=embeddings, documents=docs, metadatas=metas)
        delete_by_campaign(scope, keep_ids=ids)
    else:
        # An explicitly empty rebuild still clears the scope.
        delete_by_campaign(scope)
    return len(ids)


def load_local_kb_dir(source_dir: str):
    """Load (source_name, text) pairs from a local directory of .md files.

    The local stand-in for the SharePoint loader; swapping to SharePoint later
    only changes this function. Raises FileNotFoundError if the dir is missing.
    """
    path = Path(source_dir)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"KB source directory not found: {source_dir}")
    return [(p.name, p.read_text()) for p in sorted(path.glob("*.md"))]