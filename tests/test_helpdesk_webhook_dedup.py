"""The single-ticket webhook job must be idempotent: Zoho retries webhooks, and
re-processing a ticket we already acted on would place a duplicate AI draft.
"""

from datetime import datetime, timedelta

from sqlmodel import select

from app.jobs import helpdesk_ai_jobs
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror

T0 = datetime(2026, 8, 20, 12, 0, 0)


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class _FakeZohoClient:
    def __init__(self, modified_at=T0):
        self.modified_at = modified_at

    def get_ticket(self, ticket_id):
        return {
            "id": ticket_id,
            "channel": "Email",
            "status": "Open",
            "modifiedTime": self.modified_at.isoformat(),
        }


def _seed(session, *, action_at, zoho_modified_at=T0):
    session.add(HelpdeskTicketMirror(
        zoho_ticket_id="Z1", channel="Email", zoho_status="Open", last_synced_at=T0,
        zoho_modified_at=zoho_modified_at,
    ))
    if action_at is not None:
        session.add(HelpdeskAIAction(
            zoho_ticket_id="Z1", action_type="draft_reply", rule="test", created_at=action_at,
        ))
    session.commit()


def _run(session, monkeypatch):
    monkeypatch.setattr(helpdesk_ai_jobs, "engine", session.get_bind())
    mirror = session.exec(select(HelpdeskTicketMirror)).first()
    modified_at = mirror.zoho_modified_at if mirror else T0
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "build_zoho_desk_client",
        lambda settings: _FakeZohoClient(modified_at=modified_at),
    )
    monkeypatch.setattr(helpdesk_ai_jobs, "get_redis_connection", lambda: _FakeRedis())
    called = {"n": 0}

    def fake_process(sess, client, mirror):
        called["n"] += 1
        return HelpdeskAIAction(zoho_ticket_id="Z1", action_type="draft_reply", rule="new")

    monkeypatch.setattr(helpdesk_ai_jobs, "process_ticket", fake_process)
    result = helpdesk_ai_jobs.process_single_ticket_job("Z1")
    return result, called["n"]


def test_skips_when_already_processed_this_version(session, monkeypatch):
    # We acted AFTER Zoho last modified the ticket -> a redelivery is a no-op.
    _seed(session, action_at=T0 + timedelta(minutes=1))
    result, calls = _run(session, monkeypatch)
    assert result["status"] == "skipped"
    assert calls == 0


def test_skips_a_duplicate_delivery_even_though_the_mirror_was_resynced(
    session, monkeypatch
):
    """The webhook re-upserts the mirror before enqueueing, so our own sync
    clock always looks fresh. Only Zoho's modification time can tell a genuine
    new reply from Zoho redelivering the same event."""
    _seed(
        session,
        action_at=T0 + timedelta(minutes=1),
        zoho_modified_at=T0,          # ticket unchanged…
    )
    mirror = session.exec(select(HelpdeskTicketMirror)).first()
    mirror.last_synced_at = T0 + timedelta(hours=2)   # …but just re-synced
    session.add(mirror)
    session.commit()

    result, calls = _run(session, monkeypatch)
    assert result["status"] == "skipped"
    assert calls == 0


def test_processes_when_no_prior_action(session, monkeypatch):
    _seed(session, action_at=None)
    result, calls = _run(session, monkeypatch)
    assert result["status"] == "ok"
    assert calls == 1


def test_processes_when_ticket_updated_since_last_action(session, monkeypatch):
    # Candidate genuinely replied: Zoho's modifiedTime is newer than our last
    # decision -> re-process.
    _seed(session, action_at=T0 - timedelta(minutes=5), zoho_modified_at=T0)
    result, calls = _run(session, monkeypatch)
    assert result["status"] == "ok"
    assert calls == 1


def test_skips_when_ticket_processing_lock_is_already_held(session, monkeypatch):
    _seed(session, action_at=None)
    locked_redis = _FakeRedis()
    locked_redis.values["helpdesk:ticket:Z1:processing"] = "other-worker"

    monkeypatch.setattr(helpdesk_ai_jobs, "engine", session.get_bind())
    monkeypatch.setattr(helpdesk_ai_jobs, "get_redis_connection", lambda: locked_redis)
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "build_zoho_desk_client",
        lambda settings: _FakeZohoClient(),
    )
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "process_ticket",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not process")),
    )

    result = helpdesk_ai_jobs.process_single_ticket_job("Z1")

    assert result["status"] == "skipped"
    assert result["reason"] == "ticket is already being processed"
