"""Helpdesk knowledge base ingestion and embedding.

Reads markdown articles from the KB folder (one '## ' section = one
retrievable chunk), embeds each chunk via the inference gateway, and stores
them in a persisted ChromaDB collection. The local KB folder is one source;
SharePoint (via `chunks_from_markdown` + `sharepoint_kb_loader`) is another —
chunking, embedding, and indexing stay identical either way.
"""

import re
from pathlib import Path

import chromadb
import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.integrations.sharepoint_client import build_sharepoint_client
from app.services.markdown_kb_chunker import chunk_markdown, heading_of, slug
from app.services.sharepoint_kb_loader import load_sharepoint_kb_folder

COLLECTION_NAME = "helpdesk_kb"

# Reserved scope for universal content that applies to every ticket
# (e.g. "how do I reschedule", account/password help). A scoped query always
# includes it alongside the ticket's own tool/campaign scope — same 3-tier
# design (general + tool + campaign) as the voice KB, since the same problem
# applies here: FOT/Test Haven/Scholastica content must not cross-contaminate
# each other's answers.
GENERAL_SCOPE = "general"


class GroundingResult(BaseModel):
    """Outcome of a KB retrieval: is this question covered, and by what."""

    grounded: bool
    best_distance: float | None
    chunks: list[dict]  # [{text, source, heading, distance}], nearest first


def embed_text(text: str) -> list[float]:
    """Embed one text via the inference gateway (1024-dim vector)."""
    settings = get_settings()
    resp = httpx.post(
        f"{settings.inference_base_url}/embed",
        headers={"Authorization": f"Bearer {settings.inference_api_key}"},
        json={"text": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def load_kb_chunks(kb_dir: str) -> list[dict]:
    """One chunk per '## ' section, carrying its source file + heading."""
    chunks = []
    for path in sorted(Path(kb_dir).glob("*.md")):
        sections = re.split(r"\n(?=## )", path.read_text())
        for section in sections:
            section = section.strip()
            if not section.startswith("## "):
                continue  # skips the '# Title' preamble and editor notes
            heading = section.splitlines()[0].removeprefix("## ").strip()
            slug_ = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:60]
            chunks.append({
                "id": f"{path.stem}::{slug_}",
                "text": section,
                "source": path.name,
                "heading": heading,
            })
    return chunks


def chunks_from_markdown(text: str, source: str) -> list[dict]:
    """Turn already-loaded markdown text into the same chunk shape
    `load_kb_chunks` produces from local files — but via the SHARED
    `chunk_markdown` (any Heading 1-6 is a real question), the one unified
    authoring rule the voice KB also uses. This is what a SharePoint-authored
    `.docx` (already converted to this markdown shape by `docx_kb_loader`)
    feeds through, so an officer only ever learns one convention for both
    KBs — unlike `load_kb_chunks`, which keeps the local `.md` files' own
    "Heading 1 title, Heading 2 question" convention untouched.
    """
    chunks = []
    for chunk in chunk_markdown(text):
        heading = heading_of(chunk)
        chunks.append({
            "id": f"{Path(source).stem}::{slug(heading)}",
            "text": chunk,
            "source": source,
            "heading": heading,
        })
    return chunks


def get_kb_collection():
    """Open the persisted KB collection (raises if never synced)."""
    settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_dir)
    return client.get_collection(COLLECTION_NAME)


def _collection_for_write():
    """Open-or-create the collection — unlike get_kb_collection, a rebuild
    must succeed even the very first time, before anything has been indexed."""
    client = chromadb.PersistentClient(path=get_settings().chroma_dir)
    return client.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def delete_by_scope(scope: str) -> None:
    """Remove every chunk tagged with this scope, leaving every other scope's
    content untouched — what makes a scoped rebuild safe to run repeatedly
    without wiping the other tool/campaign content already indexed."""
    try:
        _collection_for_write().delete(where={"scope": scope})
    except Exception:
        pass  # collection doesn't exist yet — nothing to delete


def retrieve_grounding(
    question: str,
    k: int = 3,
    tool_scope: str | None = None,
    campaign_scope: str | None = None,
) -> GroundingResult:
    """Search the KB for a candidate question and judge coverage.

    Same 3-tier scoping as the voice KB: general content is always searched,
    plus the ticket's own tool scope (FOT/Test Haven/Scholastica, resolved
    server-side from classification — never asked of the candidate) and
    campaign scope, when either is known. Omit both for the old
    whole-collection behavior (a KB with only general content in it still
    works exactly as before).

    `grounded` is binary: True only when the nearest chunk's cosine
    distance beats the calibrated threshold. A retrieval miss is an
    escalation signal, not an invitation to improvise (PSA).
    """
    settings = get_settings()
    collection = get_kb_collection()

    scopes = [GENERAL_SCOPE]
    for scope in (tool_scope, campaign_scope):
        if scope and scope not in scopes:
            scopes.append(scope)
    where = {"scope": {"$in": scopes}} if len(scopes) > 1 else {"scope": GENERAL_SCOPE}

    result = collection.query(
        query_embeddings=[embed_text(question)], n_results=k, where=where
    )

    chunks = [
        {"text": doc, "source": meta["source"], "heading": meta["heading"], "distance": distance,
         "scope": meta.get("scope")}
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        )
    ]
    best_distance = chunks[0]["distance"] if chunks else None
    grounded = best_distance is not None and best_distance <= settings.kb_grounding_threshold
    return GroundingResult(grounded=grounded, best_distance=best_distance, chunks=chunks)


def rebuild_kb_index(chunks: list[dict] | None = None, scope: str = GENERAL_SCOPE) -> dict:
    """Rebuild one scope's worth of the KB: replace every chunk tagged with
    `scope`, leaving every other scope's content untouched.

    `chunks` defaults to loading the local `kb/` folder (unchanged behavior
    for existing callers, always the general scope). Pass an explicit list —
    e.g. from `rebuild_kb_index_from_sharepoint` — to index a different
    source instead: embedding and indexing don't care where the chunks came
    from.

    At tens of chunks this costs seconds of compute and guarantees the
    index always matches the source exactly for THIS scope — no stale-chunk
    bookkeeping. Cosine space, matching standard text-embedding practice.
    """
    if chunks is None:
        settings = get_settings()
        chunks = load_kb_chunks(settings.kb_dir)

    delete_by_scope(scope)
    collection = _collection_for_write()

    for chunk in chunks:
        # Scope-qualify the id (matching voice_kb_ingest.reindex_campaign's
        # `f"{scope}::{source}::..."` scheme): the SAME source document can be
        # indexed into more than one scope on purpose (e.g. one email-reply
        # doc feeding both the "fot" and "test-haven" Helpdesk scopes), and
        # chunk["id"] is otherwise just source+heading with no scope in it —
        # reusing it across scopes collided, silently overwriting one scope's
        # copy with the other's the moment both were indexed.
        #
        # upsert, not add: `add` silently no-ops on an id that already exists
        # (no error, no overwrite) rather than replacing it — harmless once
        # every chunk is scope-tagged, but it's exactly what left pre-scoping
        # chunks stuck with stale (scope-less) metadata the first time this
        # ran after scoping was introduced. upsert always replaces.
        collection.upsert(
            ids=[f"{scope}::{chunk['id']}"],
            embeddings=[embed_text(chunk["text"])],
            documents=[chunk["text"]],
            metadatas=[{"source": chunk["source"], "heading": chunk["heading"], "scope": scope}],
        )
    return {"chunks_indexed": len(chunks)}


def rebuild_kb_index_from_sharepoint(folder_path: str, scope: str = GENERAL_SCOPE) -> dict:
    """Rebuild one scope of the Helpdesk KB from a SharePoint folder of
    .docx files.

    Same shared pipeline as the voice KB's SharePoint reindex
    (`sharepoint_kb_loader.load_sharepoint_kb_folder` -> `docx_kb_loader.
    docx_to_markdown`), just fed into this KB's own chunking
    (`chunks_from_markdown`) and collection instead. Returns
    {chunks_indexed, warnings} — `warnings` lists every skipped file or
    dropped heading, from every file, so whoever authored the SharePoint
    content knows exactly what to fix.
    """
    settings = get_settings()
    client = build_sharepoint_client(settings)
    documents, warnings = load_sharepoint_kb_folder(
        client,
        hostname=settings.sharepoint_hostname,
        site_path=settings.sharepoint_site_path,
        folder_path=folder_path,
        library_name=settings.sharepoint_library_name,
    )

    chunks: list[dict] = []
    for source, text in documents:
        chunks.extend(chunks_from_markdown(text, source))

    result = rebuild_kb_index(chunks=chunks, scope=scope)
    result["warnings"] = warnings
    return result
