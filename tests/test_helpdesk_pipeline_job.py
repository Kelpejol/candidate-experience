from app.jobs import helpdesk_ai_jobs


class _FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.deleted = []

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


def test_pipeline_job_skips_when_another_pipeline_is_running(monkeypatch):
    redis = _FakeRedis({helpdesk_ai_jobs.HELPDESK_PIPELINE_RUNNING_LOCK: "other"})
    monkeypatch.setattr(helpdesk_ai_jobs, "get_redis_connection", lambda: redis)
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "build_zoho_desk_client",
        lambda settings: (_ for _ in ()).throw(AssertionError("must not build client")),
    )

    result = helpdesk_ai_jobs.run_helpdesk_pipeline_job()

    assert result == {
        "status": "skipped",
        "reason": "helpdesk pipeline already running",
    }


def test_pipeline_job_releases_running_lock(session, monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(helpdesk_ai_jobs, "get_redis_connection", lambda: redis)
    monkeypatch.setattr(helpdesk_ai_jobs, "engine", session.get_bind())
    monkeypatch.setattr(helpdesk_ai_jobs, "build_zoho_desk_client", lambda settings: object())
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "sync_ticket_mirror_from_zoho",
        lambda **kwargs: {"synced": 0, "reconciled": 0},
    )
    monkeypatch.setattr(
        helpdesk_ai_jobs,
        "process_pending_tickets",
        lambda *args, **kwargs: {},
    )

    result = helpdesk_ai_jobs.run_helpdesk_pipeline_job()

    assert result == {"status": "ok", "synced": 0, "actions": {}}
    assert helpdesk_ai_jobs.HELPDESK_PIPELINE_RUNNING_LOCK in redis.deleted
    assert helpdesk_ai_jobs.HELPDESK_PIPELINE_RUNNING_LOCK not in redis.values
