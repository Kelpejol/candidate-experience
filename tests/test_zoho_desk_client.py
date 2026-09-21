import pytest
from concurrent.futures import ThreadPoolExecutor

import app.integrations.zoho_auth as zoho_auth_module
import app.integrations.zoho_desk_client as zoho_desk_module
from app.integrations.zoho_auth import ZohoAuthError, ZohoTokenProvider
from app.integrations.zoho_desk_client import ZohoDeskClient, build_zoho_desk_client


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict | None = None,
        content: bytes | None = None,
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content if content is not None else (b"{}" if payload is not None else b"")
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeTokenProvider:
    def get_access_token(self) -> str:
        return "token_123"


def build_token_provider() -> ZohoTokenProvider:
    return ZohoTokenProvider(
        accounts_base_url="https://accounts.zoho.com",
        client_id="client_id",
        client_secret="client_secret",
        refresh_token="refresh_token",
    )


def test_token_provider_refreshes_and_caches_access_token(monkeypatch):
    calls = []

    def fake_post(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return FakeResponse(payload={"access_token": "access_1", "expires_in": 3600})

    monkeypatch.setattr(zoho_auth_module.requests, "post", fake_post)

    provider = build_token_provider()

    assert provider.get_access_token() == "access_1"
    assert provider.get_access_token() == "access_1"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://accounts.zoho.com/oauth/v2/token"
    assert calls[0]["params"]["grant_type"] == "refresh_token"


def test_token_provider_only_refreshes_once_for_concurrent_callers(monkeypatch):
    calls = []

    def fake_post(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return FakeResponse(payload={"access_token": "access_1", "expires_in": 3600})

    monkeypatch.setattr(zoho_auth_module.requests, "post", fake_post)
    provider = build_token_provider()

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(lambda _: provider.get_access_token(), range(2)))

    assert tokens == ["access_1", "access_1"]
    assert len(calls) == 1


def test_token_provider_refreshes_again_when_token_is_stale(monkeypatch):
    tokens = iter(["access_1", "access_2"])

    def fake_post(url, params=None, timeout=None):
        return FakeResponse(payload={"access_token": next(tokens), "expires_in": 3600})

    monkeypatch.setattr(zoho_auth_module.requests, "post", fake_post)

    provider = build_token_provider()
    assert provider.get_access_token() == "access_1"

    provider._expires_at = 0.0

    assert provider.get_access_token() == "access_2"


def test_token_provider_raises_on_error_payload(monkeypatch):
    def fake_post(url, params=None, timeout=None):
        return FakeResponse(payload={"error": "invalid_code"})

    monkeypatch.setattr(zoho_auth_module.requests, "post", fake_post)

    provider = build_token_provider()

    with pytest.raises(ZohoAuthError):
        provider.get_access_token()


def test_client_sends_auth_and_org_headers(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured.update(
            {"method": method, "url": url, "headers": headers, "params": params}
        )
        return FakeResponse(payload={"data": [{"id": "dept_1"}]})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    payload = client.list_departments()

    assert payload["data"][0]["id"] == "dept_1"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://desk.zoho.com/api/v1/departments"
    assert captured["headers"]["Authorization"] == "Zoho-oauthtoken token_123"
    assert captured["headers"]["orgId"] == "org_1"


def test_client_omits_org_header_when_listing_organizations(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse(payload={"data": []})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    client.list_organizations()

    assert "orgId" not in captured["headers"]


def test_client_returns_empty_data_on_204_no_content(monkeypatch):
    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        return FakeResponse(status_code=204, payload=None)

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    assert client.list_tickets() == {"data": []}


def test_thread_history_fetches_all_pages(monkeypatch):
    client = ZohoDeskClient(base_url="https://desk.zoho.com/api/v1", token_provider=FakeTokenProvider())
    offsets = []
    def request(method, path, params):
        offsets.append(params["from"])
        return {"data": [{"id": str(i)} for i in range(100)] if params["from"] == 0 else [{"id": "100"}]}
    monkeypatch.setattr(client, "_request", request)
    result = client.list_ticket_threads("ticket")
    assert len(result["data"]) == 101
    assert offsets == [0, 100]


def test_repeated_thread_page_is_rejected(monkeypatch):
    client = ZohoDeskClient(base_url="https://desk.zoho.com/api/v1", token_provider=FakeTokenProvider())
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"data": [{"id": str(i)} for i in range(100)]})
    with pytest.raises(RuntimeError, match="repeated"):
        client.list_ticket_threads("ticket")


def test_create_draft_reply_posts_email_draft(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured.update({"method": method, "url": url, "json": json})
        return FakeResponse(payload={"id": "draft_1"})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    client.create_draft_reply(
        ticket_id="ticket_1",
        content="<p>Hello</p>",
        from_email_address="support@dragnet.com",
        to="candidate@example.com",
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://desk.zoho.com/api/v1/tickets/ticket_1/draftReply"
    assert captured["json"]["channel"] == "EMAIL"
    assert captured["json"]["to"] == "candidate@example.com"


def test_create_draft_reply_never_sends_a_subject_field_to_zoho(monkeypatch):
    """Regression guard: confirmed live 2026-09-17 that Zoho's real API
    rejects `subject` outright on this endpoint ("An extra parameter
    'subject' is found", HTTP 422) — passing it here broke every live draft
    creation until reverted. Even if a caller passes `subject=`, it must
    never reach the actual Zoho payload."""
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured.update({"json": json})
        return FakeResponse(payload={"id": "draft_1"})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    client.create_draft_reply(
        ticket_id="ticket_1",
        content="<p>Hello</p>",
        from_email_address="support@dragnet.com",
        to="candidate@example.com",
        subject="Re:[## 102802 ##] Password reset",
    )

    assert "subject" not in captured["json"]


def test_send_reply_never_sends_a_subject_field_to_zoho(monkeypatch):
    """Same regression guard as create_draft_reply's, for sendReply."""
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured.update({"json": json})
        return FakeResponse(payload={"id": "sent_1"})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    client.send_reply(
        ticket_id="ticket_1",
        content="Kindly use the forgotten password option.",
        from_email_address="support@dragnet.com",
        to="candidate@example.com",
        subject="Re:[## 102802 ##] Password reset",
    )

    assert "subject" not in captured["json"]


def test_send_whatsapp_reply_posts_a_public_comment(monkeypatch):
    """Unverified against a real WhatsApp ticket (see the method's own
    docstring) — this test only pins down what OUR client currently sends,
    not that Zoho actually delivers it to the candidate over WhatsApp."""
    captured = {}

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured.update({"method": method, "url": url, "json": json})
        return FakeResponse(payload={"id": "comment_1"})

    monkeypatch.setattr(zoho_desk_module.requests, "request", fake_request)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    client.send_whatsapp_reply(ticket_id="ticket_1", content="Hi, try a different browser.")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://desk.zoho.com/api/v1/tickets/ticket_1/comments"
    assert captured["json"]["isPublic"] is True
    assert captured["json"]["content"] == "Hi, try a different browser."


def test_download_attachment_content_uses_authenticated_zoho_api_url(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers})
        return FakeResponse(content=b"image-bytes", headers={"content-type": "image/png"})

    monkeypatch.setattr(zoho_desk_module.requests, "get", fake_get)

    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    data, content_type = client.download_attachment_content(
        "/tickets/t1/threads/th1/attachments/a1/content"
    )

    assert data == b"image-bytes"
    assert content_type == "image/png"
    assert captured["url"] == (
        "https://desk.zoho.com/api/v1/tickets/t1/threads/th1/attachments/a1/content"
    )
    assert captured["headers"]["orgId"] == "org_1"


def test_download_attachment_content_rejects_non_zoho_host():
    client = ZohoDeskClient(
        base_url="https://desk.zoho.com/api/v1",
        token_provider=FakeTokenProvider(),
        org_id="org_1",
    )

    with pytest.raises(ValueError):
        client.download_attachment_content("https://example.com/file.png")


class FakeSettings:
    zoho_accounts_base_url = "https://accounts.zoho.com"
    zoho_desk_base_url = "https://desk.zoho.com/api/v1"
    zoho_client_id = "client_id"
    zoho_client_secret = "client_secret"
    zoho_refresh_token = "refresh_token"
    zoho_org_id = "org_1"
    zoho_department_id = None


def test_build_zoho_desk_client_builds_from_settings():
    client = build_zoho_desk_client(FakeSettings())

    assert client.base_url == "https://desk.zoho.com/api/v1"
    assert client.org_id == "org_1"


def test_build_zoho_desk_client_accepts_request_timeout():
    client = build_zoho_desk_client(FakeSettings(), request_timeout=7)

    assert client.request_timeout == 7
    assert client.token_provider.request_timeout == 7


def test_build_zoho_desk_client_names_missing_settings():
    settings = FakeSettings()
    settings.zoho_client_secret = None
    settings.zoho_refresh_token = None

    with pytest.raises(RuntimeError) as exc_info:
        build_zoho_desk_client(settings)

    assert "ZOHO_CLIENT_SECRET" in str(exc_info.value)
    assert "ZOHO_REFRESH_TOKEN" in str(exc_info.value)
