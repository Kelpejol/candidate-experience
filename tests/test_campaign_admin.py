from app.api.routes import campaign_admin
from app.models.campaign import Campaign


def _campaign(session, **kw):
    c = Campaign(name=kw.pop("name", "Dangote"), **kw)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def test_patch_inbound_updates_only_sent_fields(client, session):
    c = _campaign(session, inbound_active=True, kb_source=None, kb_scope="keep-me")
    resp = client.patch(
        f"/campaigns/{c.id}/inbound",
        json={"inbound_active": False, "kb_source": "kb_voice"},
    )
    assert resp.status_code == 200
    session.refresh(c)
    assert c.inbound_active is False
    assert c.kb_source == "kb_voice"
    assert c.kb_scope == "keep-me"          # not sent -> unchanged


def test_patch_inbound_404(client):
    assert client.patch("/campaigns/nope/inbound", json={"inbound_active": False}).status_code == 404


def test_reindex_404(client):
    assert client.post("/campaigns/nope/kb/reindex").status_code == 404


def test_reindex_400_without_source(client, session):
    c = _campaign(session, kb_source=None)
    assert client.post(f"/campaigns/{c.id}/kb/reindex").status_code == 400


def test_reindex_happy_path(client, session, monkeypatch):
    c = _campaign(session, kb_source="some/dir", kb_scope="dangote")
    monkeypatch.setattr(campaign_admin, "load_local_kb_dir", lambda d: [("a.md", "# Q\nA")])
    captured = {}
    monkeypatch.setattr(
        campaign_admin, "reindex_campaign",
        lambda scope, docs: captured.update(scope=scope) or 5,
    )
    resp = client.post(f"/campaigns/{c.id}/kb/reindex")
    assert resp.status_code == 200
    assert resp.json()["reindexed"] == 5
    assert captured["scope"] == "dangote"


def test_reindex_bad_source_dir_is_400(client, session):
    c = _campaign(session, kb_source="/no/such/dir/xyz", kb_scope="dangote")
    # real load_local_kb_dir raises FileNotFoundError -> endpoint maps to 400
    assert client.post(f"/campaigns/{c.id}/kb/reindex").status_code == 400


# --- SharePoint-backed reindex ---------------------------------------------

def test_reindex_from_sharepoint_404(client):
    assert (
        client.post("/campaigns/nope/kb/reindex-from-sharepoint").status_code == 404
    )


def test_reindex_from_sharepoint_400_without_source(client, session):
    c = _campaign(session, kb_source=None)
    resp = client.post(f"/campaigns/{c.id}/kb/reindex-from-sharepoint")
    assert resp.status_code == 400


def test_reindex_from_sharepoint_500_when_not_configured(client, session, monkeypatch):
    c = _campaign(session, kb_source="General FAQ", kb_scope="dangote")

    def boom(settings):
        raise RuntimeError("Missing SharePoint reader settings: KB_READER_TENANT_ID")

    monkeypatch.setattr(campaign_admin, "build_sharepoint_client", boom)
    resp = client.post(f"/campaigns/{c.id}/kb/reindex-from-sharepoint")
    assert resp.status_code == 500
    assert "KB_READER_TENANT_ID" in resp.json()["detail"]


def test_reindex_from_sharepoint_happy_path(client, session, monkeypatch):
    c = _campaign(session, kb_source="General FAQ", kb_scope="dangote")
    monkeypatch.setattr(campaign_admin, "build_sharepoint_client", lambda settings: object())

    captured = {}

    def fake_loader(client_obj, hostname, site_path, folder_path, library_name=None):
        captured["folder_path"] = folder_path
        return [("Scheduling.docx", "# Q\nA")], ["Scheduling.docx: some warning"]

    monkeypatch.setattr(campaign_admin, "load_sharepoint_kb_folder", fake_loader)
    monkeypatch.setattr(
        campaign_admin, "reindex_campaign",
        lambda scope, docs: 5,
    )

    resp = client.post(f"/campaigns/{c.id}/kb/reindex-from-sharepoint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reindexed"] == 5
    assert body["scope"] == "dangote"
    assert body["warnings"] == ["Scheduling.docx: some warning"]
    assert captured["folder_path"] == "General FAQ"  # kb_source passed through


def test_reindex_from_sharepoint_no_documents_is_400(client, session, monkeypatch):
    c = _campaign(session, kb_source="Empty Folder", kb_scope="dangote")
    monkeypatch.setattr(campaign_admin, "build_sharepoint_client", lambda settings: object())
    monkeypatch.setattr(
        campaign_admin, "load_sharepoint_kb_folder",
        lambda *a, **k: ([], ["Skipped 'notes.pdf' — only .docx files are indexed."]),
    )
    resp = client.post(f"/campaigns/{c.id}/kb/reindex-from-sharepoint")
    assert resp.status_code == 400


def test_reindex_from_sharepoint_client_error_is_502(client, session, monkeypatch):
    c = _campaign(session, kb_source="General FAQ", kb_scope="dangote")
    monkeypatch.setattr(campaign_admin, "build_sharepoint_client", lambda settings: object())

    def boom(*a, **k):
        raise Exception("403 Forbidden")

    monkeypatch.setattr(campaign_admin, "load_sharepoint_kb_folder", boom)
    resp = client.post(f"/campaigns/{c.id}/kb/reindex-from-sharepoint")
    assert resp.status_code == 502
