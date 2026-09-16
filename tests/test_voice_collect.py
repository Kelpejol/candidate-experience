import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.models.collected_datum import CollectedDatum


@pytest.fixture(autouse=True)
def _open_endpoint(monkeypatch):
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_collect_saves_caller_value(client, session):
    resp = client.post(
        "/voice-agent/collect",
        json={"field": "email", "value": "paul@example.com",
              "conversation_id": "conv1", "campaign": "dangote"},
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] is True

    d = session.exec(select(CollectedDatum)).first()
    assert d.field == "email"
    assert d.value == "paul@example.com"
    assert d.conversation_id == "conv1"
    assert d.campaign == "dangote"


def test_collect_rejects_empty_value(client):
    resp = client.post("/voice-agent/collect", json={"field": "email", "value": "   "})
    assert resp.status_code == 400
