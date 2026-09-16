"""Helpdesk KB's 3-tier scoping (general + tool + campaign) — same design
as the voice KB, and for the same reason: FOT / Test Haven / Scholastica
content must not cross-contaminate each other's grounded answers, and a
scoped reindex must never wipe out other scopes' already-indexed content.
"""

import chromadb

from app.core.config import get_settings
from app.services.helpdesk_kb_service import (
    GENERAL_SCOPE,
    delete_by_scope,
    rebuild_kb_index,
    retrieve_grounding,
)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    get_settings.cache_clear()


def _seed(scope_and_texts):
    """Directly write chunks tagged with a scope, bypassing embedding — same
    toy-vector approach the voice KB store tests use, so these never touch
    the inference gateway."""
    client = chromadb.PersistentClient(path=get_settings().chroma_dir)
    collection = client.get_or_create_collection("helpdesk_kb", metadata={"hnsw:space": "cosine"})
    for i, (scope, text) in enumerate(scope_and_texts):
        collection.add(
            ids=[f"{scope}-{i}"],
            embeddings=[[1.0, 0.0]],
            documents=[text],
            metadatas=[{"source": "test.docx", "heading": text, "scope": scope}],
        )


def _fixed_embed(monkeypatch, module):
    monkeypatch.setattr(module, "embed_text", lambda text: [1.0, 0.0])


def test_general_only_when_no_tool_or_campaign_scope_given(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([(GENERAL_SCOPE, "general answer"), ("fot", "FOT-only answer")])

    result = retrieve_grounding("anything", k=10)
    sources = {c["heading"] for c in result.chunks}
    assert sources == {"general answer"}


def test_tool_scope_adds_that_tool_content_alongside_general(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([
        (GENERAL_SCOPE, "general answer"),
        ("fot", "FOT-only answer"),
        ("test-haven", "Test Haven-only answer"),
    ])

    result = retrieve_grounding("anything", k=10, tool_scope="fot")
    sources = {c["heading"] for c in result.chunks}
    assert sources == {"general answer", "FOT-only answer"}   # never Test Haven's


def test_campaign_scope_adds_that_campaign_content_alongside_general(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([
        (GENERAL_SCOPE, "general answer"),
        ("exxonmobil", "ExxonMobil-only answer"),
        ("dangote", "Dangote-only answer"),
    ])

    result = retrieve_grounding("anything", k=10, campaign_scope="exxonmobil")
    sources = {c["heading"] for c in result.chunks}
    assert sources == {"general answer", "ExxonMobil-only answer"}


def test_tool_and_campaign_scope_combine(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([
        (GENERAL_SCOPE, "general answer"),
        ("fot", "FOT-only answer"),
        ("exxonmobil", "ExxonMobil-only answer"),
        ("dangote", "Dangote-only answer"),
    ])

    result = retrieve_grounding("anything", k=10, tool_scope="fot", campaign_scope="exxonmobil")
    sources = {c["heading"] for c in result.chunks}
    assert sources == {"general answer", "FOT-only answer", "ExxonMobil-only answer"}


def test_rebuild_kb_index_scoped_leaves_other_scopes_untouched(tmp_path, monkeypatch):
    """The whole point of scoping the rebuild: reindexing FOT must not erase
    General or Test Haven content that was indexed separately."""
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([(GENERAL_SCOPE, "old general answer"), ("test-haven", "old test haven answer")])

    rebuild_kb_index(
        chunks=[{"id": "fot::q1", "text": "# Q\nA", "source": "fot.docx", "heading": "new FOT answer"}],
        scope="fot",
    )

    general = retrieve_grounding("anything", k=10)
    assert {c["heading"] for c in general.chunks} == {"old general answer"}

    fot = retrieve_grounding("anything", k=10, tool_scope="fot")
    assert {c["heading"] for c in fot.chunks} == {"old general answer", "new FOT answer"}

    test_haven = retrieve_grounding("anything", k=10, tool_scope="test-haven")
    assert {c["heading"] for c in test_haven.chunks} == {"old general answer", "old test haven answer"}


def test_rebuild_kb_index_re_running_the_same_scope_replaces_it_not_duplicates(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)

    rebuild_kb_index(
        chunks=[{"id": "fot::q1", "text": "# Q\nA", "source": "fot.docx", "heading": "v1 answer"}],
        scope="fot",
    )
    rebuild_kb_index(
        chunks=[{"id": "fot::q1", "text": "# Q\nA2", "source": "fot.docx", "heading": "v2 answer"}],
        scope="fot",
    )

    result = retrieve_grounding("anything", k=10, tool_scope="fot")
    assert {c["heading"] for c in result.chunks} == {"v2 answer"}


def test_same_source_doc_indexed_into_two_scopes_does_not_collide(tmp_path, monkeypatch):
    """A real Option-A scenario: one shared doc (e.g. the email-script FAQ)
    deliberately gets indexed into BOTH "fot" and "test-haven" scopes. The
    chunk ids it produces are (source, heading)-based, with no scope in them
    — reusing the same id across two scopes must not let the second indexing
    silently overwrite the first's copy."""
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)

    shared_chunks = [
        {"id": "email-script::q1", "text": "# Q\nA", "source": "email.docx", "heading": "shared question"},
    ]
    rebuild_kb_index(chunks=shared_chunks, scope="fot")
    rebuild_kb_index(chunks=shared_chunks, scope="test-haven")

    fot = retrieve_grounding("anything", k=10, tool_scope="fot")
    assert {c["heading"] for c in fot.chunks} == {"shared question"}, "fot copy was overwritten"

    test_haven = retrieve_grounding("anything", k=10, tool_scope="test-haven")
    assert {c["heading"] for c in test_haven.chunks} == {"shared question"}


def test_delete_by_scope_removes_only_that_scope(tmp_path, monkeypatch):
    import app.services.helpdesk_kb_service as helpdesk_kb_service
    _isolate(tmp_path, monkeypatch)
    _fixed_embed(monkeypatch, helpdesk_kb_service)
    _seed([(GENERAL_SCOPE, "general answer"), ("fot", "FOT answer")])

    delete_by_scope("fot")

    assert {c["heading"] for c in retrieve_grounding("x", k=10).chunks} == {"general answer"}
    assert {c["heading"] for c in retrieve_grounding("x", k=10, tool_scope="fot").chunks} == {"general answer"}
