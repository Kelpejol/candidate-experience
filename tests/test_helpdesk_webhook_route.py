from app.core.config import get_settings
from app.api.routes import helpdesk_webhooks


class _FakeJob:
    id = "job-123"

    def get_status(self, refresh=True):
        return "queued"


def _configure_webhook(monkeypatch):
    monkeypatch.setenv("ZOHO_WEBHOOK_TOKEN", "secret")
    get_settings.cache_clear()


def test_zoho_webhook_enqueues_without_fetching_ticket_inline(client, monkeypatch):
    _configure_webhook(monkeypatch)
    captured = {}

    monkeypatch.setattr(helpdesk_webhooks, "get_default_queue", lambda: object())
    monkeypatch.setattr(helpdesk_webhooks, "get_redis_connection", lambda: object())

    def fake_enqueue_unique_active_job(**kwargs):
        captured.update(kwargs)
        return _FakeJob()

    monkeypatch.setattr(
        helpdesk_webhooks,
        "enqueue_unique_active_job",
        fake_enqueue_unique_active_job,
    )

    response = client.post(
        "/helpdesk/webhooks/zoho/ticket",
        json={"ticketId": "Z1"},
        headers={"X-Webhook-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "zoho_ticket_id": "Z1",
        "ai_job_id": "job-123",
        "ai_job_status": "queued",
    }
    assert captured["args"] == ("Z1",)
    assert captured["lock_key"] == "helpdesk:ticket:Z1:ai-job"
    assert captured["func"] is helpdesk_webhooks.process_single_ticket_job

    get_settings.cache_clear()


def test_zoho_webhook_returns_503_when_enqueue_fails(client, monkeypatch):
    _configure_webhook(monkeypatch)

    monkeypatch.setattr(helpdesk_webhooks, "get_default_queue", lambda: object())
    monkeypatch.setattr(helpdesk_webhooks, "get_redis_connection", lambda: object())

    def fail_enqueue(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(helpdesk_webhooks, "enqueue_unique_active_job", fail_enqueue)

    response = client.post(
        "/helpdesk/webhooks/zoho/ticket",
        json={"ticketId": "Z1"},
        headers={"X-Webhook-Token": "secret"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "could not enqueue helpdesk ticket job"

    get_settings.cache_clear()


def test_zoho_webhook_rejects_bad_token(client, monkeypatch):
    _configure_webhook(monkeypatch)

    response = client.post(
        "/helpdesk/webhooks/zoho/ticket",
        json={"ticketId": "Z1"},
        headers={"X-Webhook-Token": "wrong"},
    )

    assert response.status_code == 401

    get_settings.cache_clear()
