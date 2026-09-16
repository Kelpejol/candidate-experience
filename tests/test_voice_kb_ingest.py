import pytest

from app.services.voice_kb_ingest import chunk_markdown
from app.core.config import get_settings
from app.services import voice_kb_ingest
from app.services.voice_kb_store import delete_by_campaign, query

SAMPLE = """# How do I reschedule my exam?
Contact support at least 48 hours before your scheduled slot.

# What browser should I use?
Use the latest Chrome or Firefox. Disable pop-up blockers.
"""


def test_splits_on_headings_into_self_contained_chunks():
    chunks = chunk_markdown(SAMPLE)
    assert len(chunks) == 2                     # one per heading
    assert chunks[0].startswith("# How do I reschedule")
    assert "48 hours" in chunks[0]              # heading + its body stay together
    assert "browser" in chunks[1]


def test_ignores_empty_input():
    assert chunk_markdown("") == []


def test_index_document_embeds_chunks_and_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    get_settings.cache_clear()

    # Fake the gateway so the test is offline + deterministic.
    def fake_embed(text):
        return [1.0, 0.0] if "reschedule" in text.lower() else [0.0, 1.0]
    monkeypatch.setattr(voice_kb_ingest, "embed_text", fake_embed)

    n = voice_kb_ingest.index_document(SAMPLE, source="faq.md")

    assert n == 2
    hits = query([0.9, 0.1], k=1)                 # query vector leans "reschedule"
    assert "reschedule" in hits[0]["document"].lower()
    assert hits[0]["metadata"]["source"] == "faq.md"
    get_settings.cache_clear()


def _isolate(tmp_path, monkeypatch, embed=lambda t: [1.0, 0.0]):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(voice_kb_ingest, "embed_text", embed)


def test_index_document_tags_campaign(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    n = voice_kb_ingest.index_document(SAMPLE, source="faq.md", campaign="dangote")
    assert n == 2
    hits = query([1.0, 0.0], k=10, campaign="dangote")
    assert hits and all(h["metadata"]["campaign"] == "dangote" for h in hits)
    # A different campaign with no general content sees none of Dangote's chunks.
    assert query([1.0, 0.0], k=10, campaign="nnpc") == []
    get_settings.cache_clear()


def test_clean_reindex_removes_stale_chunks(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    voice_kb_ingest.index_document(SAMPLE, source="faq.md", campaign="dangote")
    assert len(query([1.0, 0.0], k=10, campaign="dangote")) == 2

    # Content edited down to one section -> clean re-index is delete + index.
    delete_by_campaign("dangote")
    voice_kb_ingest.index_document(
        "# Only question left?\nOnly answer.", source="faq.md", campaign="dangote"
    )
    hits = query([1.0, 0.0], k=10, campaign="dangote")
    assert len(hits) == 1                       # the removed section is gone, not stale
    assert "Only question" in hits[0]["document"]
    get_settings.cache_clear()


def test_empty_document_indexes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert voice_kb_ingest.index_document("", source="faq.md", campaign="dangote") == 0
    assert query([1.0, 0.0], k=10, campaign="dangote") == []
    get_settings.cache_clear()


def test_embed_failure_writes_nothing(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 2:                     # second chunk's embedding fails
            raise RuntimeError("gateway down")
        return [1.0, 0.0]

    _isolate(tmp_path, monkeypatch, embed=flaky)
    with pytest.raises(RuntimeError):
        voice_kb_ingest.index_document(SAMPLE, source="faq.md", campaign="dangote")
    # First chunk embedded fine, but nothing is written — no partial index.
    assert query([1.0, 0.0], k=10, campaign="dangote") == []
    get_settings.cache_clear()


# --- reindex_campaign (atomic replace) ------------------------------------

def test_reindex_campaign_replaces_content(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    voice_kb_ingest.reindex_campaign("dangote", [("faq.md", SAMPLE)])   # 2 chunks
    voice_kb_ingest.reindex_campaign("dangote", [("faq.md", "# Only one left?\nYes.")])
    hits = query([1.0, 0.0], k=10, campaign="dangote")
    assert len(hits) == 1 and "Only one" in hits[0]["document"]
    get_settings.cache_clear()


def test_reindex_is_atomic_on_embed_failure(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    voice_kb_ingest.reindex_campaign("dangote", [("faq.md", SAMPLE)])   # seed 2 chunks
    assert len(query([1.0, 0.0], k=10, campaign="dangote")) == 2

    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("gateway down mid-reindex")
        return [1.0, 0.0]

    monkeypatch.setattr(voice_kb_ingest, "embed_text", flaky)
    with pytest.raises(RuntimeError):
        voice_kb_ingest.reindex_campaign("dangote", [("faq.md", SAMPLE)])
    # The existing KB must NOT be wiped — the delete only runs after all embeds.
    assert len(query([1.0, 0.0], k=10, campaign="dangote")) == 2
    get_settings.cache_clear()


def test_reindex_empty_clears_campaign(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    voice_kb_ingest.reindex_campaign("dangote", [("faq.md", SAMPLE)])
    assert voice_kb_ingest.reindex_campaign("dangote", []) == 0
    assert query([1.0, 0.0], k=10, campaign="dangote") == []
    get_settings.cache_clear()


def test_load_local_kb_dir_reads_md_only(tmp_path):
    (tmp_path / "a.md").write_text("# Q\nA")
    (tmp_path / "note.txt").write_text("ignore me")
    assert voice_kb_ingest.load_local_kb_dir(str(tmp_path)) == [("a.md", "# Q\nA")]


def test_load_local_kb_dir_missing_raises():
    with pytest.raises(FileNotFoundError):
        voice_kb_ingest.load_local_kb_dir("/no/such/dir/xyz123")
