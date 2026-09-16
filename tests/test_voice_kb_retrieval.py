from app.core.config import get_settings
from app.services import voice_kb_retrieval
from app.services.voice_kb_store import GENERAL_SCOPE, upsert_chunks


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    get_settings.cache_clear()
    upsert_chunks(
        ids=["r"], embeddings=[[1.0, 0.0]],
        documents=["reschedule info"],
        metadatas=[{"source": "faq.md", "heading": "Reschedule", "campaign": GENERAL_SCOPE}],
    )


def test_grounded_when_question_is_close(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(voice_kb_retrieval, "embed_text", lambda q, timeout=None: [1.0, 0.0])  # identical → dist ~0
    out = voice_kb_retrieval.retrieve_for_answer("move my exam", threshold=0.55)
    assert out["answer_available"] is True
    assert out["content"] == ["reschedule info"]
    get_settings.cache_clear()


def test_escalates_when_question_is_far(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(voice_kb_retrieval, "embed_text", lambda q, timeout=None: [0.0, 1.0])  # orthogonal → dist ~1
    out = voice_kb_retrieval.retrieve_for_answer("capital of France", threshold=0.55)
    assert out["answer_available"] is False
    assert out["content"] is None
    get_settings.cache_clear()


def test_retrieve_threads_campaign_to_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(voice_kb_retrieval, "embed_text", lambda q, timeout=None: [1.0, 0.0])
    monkeypatch.setattr(
        voice_kb_retrieval, "query",
        lambda emb, k=3, campaign=None, extra_scopes=None: captured.update(campaign=campaign) or [],
    )
    voice_kb_retrieval.retrieve_for_answer("q", campaign="dangote")
    assert captured["campaign"] == "dangote"
