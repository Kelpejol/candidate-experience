from app.api.routes import voice_kb
from app.core.config import get_settings




def test_kb_query_returns_grounded_answer(client, monkeypatch):
    # These test behavior, not auth — disable the token so no header is needed.
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    monkeypatch.setattr(voice_kb, "retrieve_for_answer", lambda question, k=3, campaign=None, extra_scopes=None: {
        "answer_available": True, "content": ["reschedule info"],
        "sources": ["faq.md"], "best_distance": 0.41,
    })
    resp = client.post("/voice-agent/kb/query", json={"question": "move my exam"})
    assert resp.status_code == 200
    assert resp.json()["answer_available"] is True
    assert resp.json()["content"] == ["reschedule info"]
    get_settings.cache_clear()


def test_kb_query_signals_escalation(client, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    monkeypatch.setattr(voice_kb, "retrieve_for_answer", lambda question, k=3, campaign=None, extra_scopes=None: {
        "answer_available": False, "content": None, "sources": [], "best_distance": 0.73,
    })
    resp = client.post("/voice-agent/kb/query", json={"question": "capital of France"})
    assert resp.json()["answer_available"] is False
    get_settings.cache_clear()



def test_kb_query_gateway_failure_escalates_not_500(client, monkeypatch):
    # A live-call gateway/vector blip must degrade to "escalate", never a 500.
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()

    def boom(question, k=3, campaign=None, extra_scopes=None):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(voice_kb, "retrieve_for_answer", boom)
    resp = client.post("/voice-agent/kb/query", json={"question": "move my exam"})
    assert resp.status_code == 200
    assert resp.json()["answer_available"] is False
    get_settings.cache_clear()


def test_rejects_missing_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "s3cret")
    get_settings.cache_clear()
    resp = client.post("/voice-agent/kb/query", json={"question": "x"})
    assert resp.status_code == 401
    get_settings.cache_clear()


def test_accepts_correct_token(client, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "s3cret")
    get_settings.cache_clear()
    monkeypatch.setattr(voice_kb, "retrieve_for_answer", lambda question, k=3, campaign=None, extra_scopes=None: {
        "answer_available": False, "content": None, "sources": [], "best_distance": None,
    })
    resp = client.post(
        "/voice-agent/kb/query",
        json={"question": "x"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert resp.status_code == 200
    get_settings.cache_clear()


def test_kb_query_resolves_active_campaign_to_scope(client, session, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    from app.models.campaign import Campaign
    session.add(Campaign(name="Dangote PRP", kb_scope="dangote", inbound_active=True))
    session.commit()

    captured = {}
    monkeypatch.setattr(voice_kb, "retrieve_for_answer",
        lambda question, k=3, campaign=None, extra_scopes=None: captured.update(campaign=campaign) or {
            "answer_available": False, "content": None, "sources": [], "best_distance": None,
        })
    resp = client.post("/voice-agent/kb/query", json={"question": "x", "campaign": "dangote"})
    assert resp.status_code == 200
    assert captured["campaign"] == "dangote"       # resolved to the active scope


def test_kb_query_resolves_tool_scope_from_campaign(client, session, monkeypatch):
    """The candidate never states their test tool — it's resolved silently
    from the campaign they DO mention, and passed through as an extra scope."""
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    from app.models.campaign import Campaign
    session.add(Campaign(
        name="ExxonMobil Grad", kb_scope="exxonmobil",
        tool_name="Test Haven", inbound_active=True,
    ))
    session.commit()

    captured = {}
    monkeypatch.setattr(
        voice_kb, "retrieve_for_answer",
        lambda question, k=3, campaign=None, extra_scopes=None: captured.update(
            campaign=campaign, extra_scopes=extra_scopes
        ) or {"answer_available": False, "content": None, "sources": [], "best_distance": None},
    )
    resp = client.post(
        "/voice-agent/kb/query",
        json={"question": "can I use a phone instead of a laptop?", "campaign": "exxonmobil"},
    )
    assert resp.status_code == 200
    assert captured["campaign"] == "exxonmobil"
    assert captured["extra_scopes"] == ["test-haven"]


def test_kb_query_no_tool_scope_when_campaign_has_no_tool(client, session, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    from app.models.campaign import Campaign
    session.add(Campaign(name="Generic Campaign", inbound_active=True))
    session.commit()

    captured = {}
    monkeypatch.setattr(
        voice_kb, "retrieve_for_answer",
        lambda question, k=3, campaign=None, extra_scopes=None: captured.update(
            extra_scopes=extra_scopes
        ) or {"answer_available": False, "content": None, "sources": [], "best_distance": None},
    )
    resp = client.post(
        "/voice-agent/kb/query", json={"question": "x", "campaign": "generic campaign"},
    )
    assert resp.status_code == 200
    assert captured["extra_scopes"] is None


def test_kb_query_unknown_campaign_falls_back_to_general(client, monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    captured = {}
    monkeypatch.setattr(voice_kb, "retrieve_for_answer",
        lambda question, k=3, campaign=None, extra_scopes=None: captured.update(campaign=campaign) or {
            "answer_available": False, "content": None, "sources": [], "best_distance": None,
        })
    resp = client.post("/voice-agent/kb/query", json={"question": "x", "campaign": "nope"})
    assert resp.status_code == 200
    assert captured["campaign"] is None            # unknown -> general fallback
