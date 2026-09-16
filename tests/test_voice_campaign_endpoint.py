import pytest

from app.core.config import get_settings
from app.models.campaign import Campaign


@pytest.fixture(autouse=True)
def _open_endpoint(monkeypatch):
    # The resolve endpoint shares the tool-token guard; open it for these tests.
    monkeypatch.setenv("VOICE_KB_TOOL_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_resolve_found_returns_canonical_name(client, session):
    session.add(Campaign(name="Dangote PRP Graduate", kb_scope="dangote", inbound_active=True))
    session.commit()
    resp = client.post("/voice-agent/campaign/resolve", json={"name": "dangote"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "found"
    assert body["name"] == "Dangote PRP Graduate"


def test_resolve_not_found(client):
    resp = client.post("/voice-agent/campaign/resolve", json={"name": "nobody"})
    assert resp.json()["status"] == "not_found"


def test_resolve_inactive(client, session):
    session.add(Campaign(name="NNPC Feedback", inbound_active=False))
    session.commit()
    resp = client.post("/voice-agent/campaign/resolve", json={"name": "nnpc"})
    assert resp.json()["status"] == "inactive"


def test_resolve_ambiguous(client, session):
    session.add(Campaign(name="Dangote Graduate", inbound_active=True))
    session.add(Campaign(name="Dangote Experienced", inbound_active=True))
    session.commit()
    resp = client.post("/voice-agent/campaign/resolve", json={"name": "dangote"})
    assert resp.json()["status"] == "ambiguous"
