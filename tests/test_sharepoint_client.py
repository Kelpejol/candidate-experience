"""SharePointClient's drive-by-name resolution.

A site's DEFAULT document library needs no lookup, but a KB kept in its own,
non-default library (this deployment's "Candidate experience KB") can only be
reached by first resolving its name to a Graph drive id — get this wrong and
every file listing silently queries the wrong (likely empty) library instead
of erroring.
"""

import app.integrations.sharepoint_client as sharepoint_client_module
from app.integrations.sharepoint_client import SharePointClient


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeMsalApp:
    """SharePointClient.__init__ builds a real MSAL ConfidentialClientApplication,
    which performs live tenant discovery over the network immediately — before
    a test ever reaches `_get_access_token`. Stub the whole class so
    constructing a client in a test never touches the network."""

    def __init__(self, *args, **kwargs):
        pass


def build_client(monkeypatch):
    monkeypatch.setattr(sharepoint_client_module.msal, "ConfidentialClientApplication", _FakeMsalApp)
    client = SharePointClient(tenant_id="t", client_id="c", client_secret="s")
    monkeypatch.setattr(client, "_get_access_token", lambda: "token")
    return client


def test_get_drive_id_finds_a_matching_library_case_insensitively(monkeypatch):
    client = build_client(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResponse(payload={"value": [
            {"id": "drive-default", "name": "Documents"},
            {"id": "drive-kb", "name": "Candidate experience KB"},
        ]})

    monkeypatch.setattr("app.integrations.sharepoint_client.requests.get", fake_get)
    drive_id = client.get_drive_id("site1", "candidate experience kb")
    assert drive_id == "drive-kb"
    assert captured["url"] == "https://graph.microsoft.com/v1.0/sites/site1/drives"


def test_get_drive_id_raises_with_the_real_names_on_a_miss(monkeypatch):
    client = build_client(monkeypatch)
    monkeypatch.setattr(
        "app.integrations.sharepoint_client.requests.get",
        lambda url, headers=None, timeout=None: FakeResponse(
            payload={"value": [{"id": "d1", "name": "Documents"}]}
        ),
    )
    try:
        client.get_drive_id("site1", "Typo'd Library")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Typo'd Library" in str(exc)
        assert "Documents" in str(exc)


def test_list_drive_items_uses_the_named_drive_when_given(monkeypatch):
    client = build_client(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResponse(payload={"value": []})

    monkeypatch.setattr("app.integrations.sharepoint_client.requests.get", fake_get)
    client.list_drive_items("site1", "General", drive_id="drive-kb")
    assert captured["url"] == "https://graph.microsoft.com/v1.0/drives/drive-kb/root:/General:/children"


def test_list_drive_items_falls_back_to_the_default_drive_without_drive_id(monkeypatch):
    """Every existing caller that never passes drive_id must keep hitting the
    exact same default-library endpoint as before — no behaviour change."""
    client = build_client(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResponse(payload={"value": []})

    monkeypatch.setattr("app.integrations.sharepoint_client.requests.get", fake_get)
    client.list_drive_items("site1", "General")
    assert captured["url"] == "https://graph.microsoft.com/v1.0/sites/site1/drive/root:/General:/children"


def test_download_file_uses_the_named_drive_when_given(monkeypatch):
    client = build_client(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        r = FakeResponse()
        r.content = b"file-bytes"
        return r

    monkeypatch.setattr("app.integrations.sharepoint_client.requests.get", fake_get)
    data = client.download_file("site1", "item1", drive_id="drive-kb")
    assert data == b"file-bytes"
    assert captured["url"] == "https://graph.microsoft.com/v1.0/drives/drive-kb/items/item1/content"


def test_download_file_falls_back_to_the_default_drive_without_drive_id(monkeypatch):
    client = build_client(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        r = FakeResponse()
        r.content = b"file-bytes"
        return r

    monkeypatch.setattr("app.integrations.sharepoint_client.requests.get", fake_get)
    client.download_file("site1", "item1")
    assert captured["url"] == "https://graph.microsoft.com/v1.0/sites/site1/drive/items/item1/content"
