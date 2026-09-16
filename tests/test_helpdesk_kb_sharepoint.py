"""Helpdesk KB, SharePoint path: chunks_from_markdown must use the SAME
unified authoring rule as the voice KB (any Heading 1-6 is a real question)
— not the local .md loader's "Heading 1 = title, Heading 2 = question"
convention, which stays untouched for existing local files.
"""

from app.services import helpdesk_kb_service
from app.services.helpdesk_kb_service import (
    chunks_from_markdown,
    load_kb_chunks,
    rebuild_kb_index,
    rebuild_kb_index_from_sharepoint,
)


def test_chunks_from_markdown_treats_heading_1_as_a_real_question():
    """Unlike load_kb_chunks (local .md), a Heading-1-equivalent line here IS
    a real question — the unified SharePoint-authoring rule, matching voice."""
    text = "# How can I reschedule my exam?\nSend us a request."
    chunks = chunks_from_markdown(text, source="Scheduling.docx")

    assert len(chunks) == 1
    assert chunks[0]["heading"] == "How can I reschedule my exam?"
    assert chunks[0]["source"] == "Scheduling.docx"
    assert chunks[0]["id"] == "Scheduling::how-can-i-reschedule-my-exam"


def test_chunks_from_markdown_handles_both_heading_1_and_2():
    text = (
        "# How can I reschedule my exam?\nSend us a request.\n"
        "## I missed my test window\nDepends on the client."
    )
    chunks = chunks_from_markdown(text, source="Scheduling.docx")
    assert len(chunks) == 2
    assert chunks[0]["heading"] == "How can I reschedule my exam?"
    assert chunks[1]["heading"] == "I missed my test window"


def test_local_md_convention_is_unaffected(tmp_path):
    """The existing kb/*.md files' "# Title (skip) + ## Question" convention
    must keep working exactly as before — this is dev/demo tooling, not the
    officer-facing standard, and shouldn't change."""
    (tmp_path / "scheduling.md").write_text(
        "# Scheduling\n> DRAFT CONTENT — internal note.\n\n"
        "## Candidate requests a reschedule\nAcknowledge with empathy.\n"
    )
    chunks = load_kb_chunks(str(tmp_path))
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Candidate requests a reschedule"
    # The title/preamble never became its own chunk.
    assert not any("Scheduling" == c["heading"] for c in chunks)


def test_rebuild_kb_index_accepts_explicit_chunks(monkeypatch):
    """rebuild_kb_index must not fall back to the local kb/ folder when an
    explicit chunk list is passed — that's what makes it source-agnostic."""
    added = []

    class _FakeCollection:
        def upsert(self, ids, embeddings, documents, metadatas):
            added.append(ids[0])

        def delete(self, where=None):
            pass

    class _FakeClient:
        def get_or_create_collection(self, name, metadata=None):
            return _FakeCollection()

    monkeypatch.setattr(
        helpdesk_kb_service.chromadb, "PersistentClient", lambda path: _FakeClient()
    )
    monkeypatch.setattr(helpdesk_kb_service, "embed_text", lambda text: [0.1])
    monkeypatch.setattr(
        helpdesk_kb_service, "load_kb_chunks",
        lambda kb_dir: (_ for _ in ()).throw(AssertionError("must not load local kb/")),
    )

    result = rebuild_kb_index(chunks=[
        {"id": "x::1", "text": "# Q\nA", "source": "x.docx", "heading": "Q"},
    ])
    assert result["chunks_indexed"] == 1
    assert added == ["general::x::1"]   # scope-qualified id (default scope)


def test_rebuild_kb_index_from_sharepoint_wires_loader_to_chunks(monkeypatch):
    monkeypatch.setattr(
        helpdesk_kb_service, "build_sharepoint_client", lambda settings: object()
    )
    monkeypatch.setattr(
        helpdesk_kb_service, "load_sharepoint_kb_folder",
        lambda client, hostname, site_path, folder_path, library_name=None: (
            [("Scheduling.docx", "# How can I reschedule?\nSend a request.")],
            ["Scheduling.docx: some warning"],
        ),
    )
    captured = {}
    monkeypatch.setattr(
        helpdesk_kb_service, "rebuild_kb_index",
        lambda chunks, scope="general": captured.update(chunks=chunks) or {"chunks_indexed": len(chunks)},
    )

    result = rebuild_kb_index_from_sharepoint("Helpdesk FAQ")

    assert result["chunks_indexed"] == 1
    assert result["warnings"] == ["Scheduling.docx: some warning"]
    assert captured["chunks"][0]["source"] == "Scheduling.docx"
    assert captured["chunks"][0]["heading"] == "How can I reschedule?"
