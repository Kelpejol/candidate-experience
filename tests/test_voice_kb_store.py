from app.core.config import get_settings
from app.services.voice_kb_store import (
    GENERAL_SCOPE,
    delete_by_campaign,
    query,
    upsert_chunks,
)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path))
    get_settings.cache_clear()


def test_store_roundtrip_finds_nearest(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # Toy 2-D vectors tagged general — no embedding gateway involved.
    upsert_chunks(
        ids=["cats", "dogs"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        documents=["all about cats", "all about dogs"],
        metadatas=[{"campaign": GENERAL_SCOPE}, {"campaign": GENERAL_SCOPE}],
    )
    hits = query([0.9, 0.1], k=1)   # leans toward "cats"
    assert hits[0]["id"] == "cats"
    assert hits[0]["document"] == "all about cats"
    get_settings.cache_clear()


def _seed_three(tmp_path, monkeypatch):
    """One chunk each in campaigns 'a', 'b', and 'general' — same vector."""
    _isolate(tmp_path, monkeypatch)
    upsert_chunks(
        ids=["a1", "b1", "g1"],
        embeddings=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        documents=["campaign A doc", "campaign B doc", "general doc"],
        metadatas=[{"campaign": "a"}, {"campaign": "b"}, {"campaign": GENERAL_SCOPE}],
    )


def test_scoped_query_returns_campaign_plus_general_only(tmp_path, monkeypatch):
    _seed_three(tmp_path, monkeypatch)
    ids = {h["id"] for h in query([1.0, 0.0], k=10, campaign="a")}
    assert ids == {"a1", "g1"}          # campaign A + general, never B
    get_settings.cache_clear()


def test_unscoped_query_returns_general_only(tmp_path, monkeypatch):
    _seed_three(tmp_path, monkeypatch)
    ids = {h["id"] for h in query([1.0, 0.0], k=10)}   # no campaign
    assert ids == {"g1"}
    get_settings.cache_clear()


def test_delete_by_campaign_removes_only_that_campaign(tmp_path, monkeypatch):
    _seed_three(tmp_path, monkeypatch)
    delete_by_campaign("a")
    assert {h["id"] for h in query([1.0, 0.0], k=10, campaign="a")} == {"g1"}   # a gone
    assert {h["id"] for h in query([1.0, 0.0], k=10, campaign="b")} == {"b1", "g1"}  # b intact
    get_settings.cache_clear()


def test_unknown_campaign_still_gets_general_fallback(tmp_path, monkeypatch):
    # An unrecognised campaign name shouldn't blank out — general still applies.
    _seed_three(tmp_path, monkeypatch)
    ids = {h["id"] for h in query([1.0, 0.0], k=10, campaign="does-not-exist")}
    assert ids == {"g1"}
    get_settings.cache_clear()


# --- extra_scopes (the third KB tier: general + tool + campaign) -----------

def _seed_with_tool(tmp_path, monkeypatch):
    """campaign 'a', tool 'test-haven', and 'general' — all distinct chunks."""
    _isolate(tmp_path, monkeypatch)
    upsert_chunks(
        ids=["a1", "tool1", "g1", "b1"],
        embeddings=[[1.0, 0.0]] * 4,
        documents=["campaign A doc", "test-haven doc", "general doc", "campaign B doc"],
        metadatas=[
            {"campaign": "a"}, {"campaign": "test-haven"},
            {"campaign": GENERAL_SCOPE}, {"campaign": "b"},
        ],
    )


def test_extra_scopes_adds_tool_content_alongside_campaign_and_general(
    tmp_path, monkeypatch
):
    _seed_with_tool(tmp_path, monkeypatch)
    ids = {
        h["id"]
        for h in query([1.0, 0.0], k=10, campaign="a", extra_scopes=["test-haven"])
    }
    assert ids == {"a1", "tool1", "g1"}   # never campaign B's content
    get_settings.cache_clear()


def test_extra_scopes_ignored_without_a_campaign(tmp_path, monkeypatch):
    # No campaign to derive a tool scope from -> extra_scopes has no effect.
    _seed_with_tool(tmp_path, monkeypatch)
    ids = {h["id"] for h in query([1.0, 0.0], k=10, extra_scopes=["test-haven"])}
    assert ids == {"g1"}
    get_settings.cache_clear()


def test_extra_scopes_none_behaves_exactly_as_before(tmp_path, monkeypatch):
    _seed_with_tool(tmp_path, monkeypatch)
    ids = {h["id"] for h in query([1.0, 0.0], k=10, campaign="a")}
    assert ids == {"a1", "g1"}   # no tool scope added -> unchanged behavior
    get_settings.cache_clear()
