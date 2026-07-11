"""Helpdesk knowledge base ingestion and embedding.

Reads markdown articles from the KB folder (one '## ' section = one
retrievable chunk), embeds each chunk via the inference gateway, and stores
them in a persisted ChromaDB collection. The KB folder is the source of
truth today; when SharePoint access lands, only `load_kb_chunks` changes —
chunking, embedding, and indexing stay identical.
"""

import re
from pathlib import Path

import chromadb
import httpx
from pydantic import BaseModel

from app.core.config import get_settings

COLLECTION_NAME = "helpdesk_kb"


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
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:60]
            chunks.append({
                "id": f"{path.stem}::{slug}",
                "text": section,
                "source": path.name,
                "heading": heading,
            })
    return chunks


def get_kb_collection():
    """Open the persisted KB collection (raises if never synced)."""
    settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_dir)
    return client.get_collection(COLLECTION_NAME)


def retrieve_grounding(question: str, k: int = 3) -> GroundingResult:
    """Search the KB for a candidate question and judge coverage.

    `grounded` is binary: True only when the nearest chunk's cosine
    distance beats the calibrated threshold. A retrieval miss is an
    escalation signal, not an invitation to improvise (PSA).
    """
    settings = get_settings()
    collection = get_kb_collection()
    result = collection.query(query_embeddings=[embed_text(question)], n_results=k)

    chunks = [
        {"text": doc, "source": meta["source"], "heading": meta["heading"], "distance": distance}
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        )
    ]
    best_distance = chunks[0]["distance"] if chunks else None
    grounded = best_distance is not None and best_distance <= settings.kb_grounding_threshold
    return GroundingResult(grounded=grounded, best_distance=best_distance, chunks=chunks)


def rebuild_kb_index() -> dict:
    """Full rebuild: wipe the collection and re-embed every chunk.

    At tens of chunks this costs seconds of compute and guarantees the
    index always matches the KB folder exactly — no stale-chunk
    bookkeeping. Cosine space, matching standard text-embedding practice.
    """
    settings = get_settings()
    chunks = load_kb_chunks(settings.kb_dir)

    client = chromadb.PersistentClient(path=settings.chroma_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    for chunk in chunks:
        collection.add(
            ids=[chunk["id"]],
            embeddings=[embed_text(chunk["text"])],
            documents=[chunk["text"]],
            metadatas=[{"source": chunk["source"], "heading": chunk["heading"]}],
        )
    return {"chunks_indexed": len(chunks)}
