from datetime import datetime, timedelta

from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services import campaign_orchestration_service as orch
from app.services.campaign_orchestration_service import (
    ACTION_BUILD_QUEUE,
    ACTION_COMPLETE,
    ACTION_DRAIN_CALLS,
    ACTION_NONE,
    ACTION_RETRY_QUEUE,
    ACTION_SYNC_RESPONSES,
    ACTION_WAIT,
    advance_campaign,
    decide_campaign_next_step,
)

NOW = datetime(2026, 8, 15, 12, 0, 0)


def make_campaign(status, survey_sent_at=None, response_wait_hours=24):
    return Campaign(
        name="Test", status=status,
        survey_sent_at=survey_sent_at, response_wait_hours=response_wait_hours,
    )


# --- survey_sent: gated on the response wait window --------------------------

def test_survey_sent_waits_until_window_elapses():
    c = make_campaign("survey_sent", survey_sent_at=NOW - timedelta(hours=1),
                      response_wait_hours=24)
    assert decide_campaign_next_step(c, NOW) == ACTION_WAIT


def test_survey_sent_syncs_after_window():
    c = make_campaign("survey_sent", survey_sent_at=NOW - timedelta(hours=25),
                      response_wait_hours=24)
    assert decide_campaign_next_step(c, NOW) == ACTION_SYNC_RESPONSES


def test_waiting_for_responses_behaves_like_survey_sent():
    # Same orchestration situation as survey_sent — must not be stranded.
    waiting = make_campaign("waiting_for_responses",
                            survey_sent_at=NOW - timedelta(hours=1))
    assert decide_campaign_next_step(waiting, NOW) == ACTION_WAIT
    elapsed = make_campaign("waiting_for_responses",
                            survey_sent_at=NOW - timedelta(hours=25))
    assert decide_campaign_next_step(elapsed, NOW) == ACTION_SYNC_RESPONSES


def test_transient_states_are_not_orchestrated():
    for status in ("survey_sending", "non_response_checking", "failed"):
        assert decide_campaign_next_step(make_campaign(status), NOW) == ACTION_NONE


def test_survey_sent_syncs_exactly_at_boundary():
    c = make_campaign("survey_sent", survey_sent_at=NOW - timedelta(hours=24),
                      response_wait_hours=24)
    assert decide_campaign_next_step(c, NOW) == ACTION_SYNC_RESPONSES


def test_survey_sent_without_timestamp_waits():
    c = make_campaign("survey_sent", survey_sent_at=None)
    assert decide_campaign_next_step(c, NOW) == ACTION_WAIT


# --- outbound_ready -> build queue ------------------------------------------

def test_outbound_ready_builds_queue():
    c = make_campaign("outbound_ready")
    assert decide_campaign_next_step(c, NOW) == ACTION_BUILD_QUEUE


# --- outbound_calling: drain / wait / complete ------------------------------

def test_outbound_calling_drains_while_queued():
    c = make_campaign("outbound_calling")
    assert decide_campaign_next_step(c, NOW, queued_count=3) == ACTION_DRAIN_CALLS


def test_outbound_calling_waits_while_calls_in_flight():
    c = make_campaign("outbound_calling")
    assert decide_campaign_next_step(c, NOW, queued_count=0, in_flight_count=2) == ACTION_WAIT


def test_outbound_calling_completes_when_nothing_left():
    c = make_campaign("outbound_calling")
    assert decide_campaign_next_step(c, NOW, queued_count=0, in_flight_count=0) == ACTION_COMPLETE


# --- non-orchestrated states ------------------------------------------------

def test_draft_and_uploaded_and_completed_are_not_orchestrated():
    for status in ("draft", "uploaded", "completed"):
        assert decide_campaign_next_step(make_campaign(status), NOW) == ACTION_NONE


# --- Part 2: advance_campaign executes the decided action -------------------

def persist_campaign(session, **kw):
    c = make_campaign(**kw)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


_attempt_seq = 0


def add_attempt(session, campaign_id, status):
    # Distinct candidate per attempt — the unique constraint (campaign,
    # candidate, attempt_number) forbids reusing one candidate id here.
    global _attempt_seq
    _attempt_seq += 1
    session.add(OutboundCallAttempt(
        campaign_id=campaign_id, candidate_id=f"cand{_attempt_seq}",
        phone="+2348010000000", status=status,
    ))
    session.commit()


def test_advance_waits_before_window(session, monkeypatch):
    called = []
    monkeypatch.setattr(orch, "sync_campaign_survey_responses_for_campaign",
                        lambda *a, **k: called.append("sync"))
    c = persist_campaign(session, status="survey_sent",
                         survey_sent_at=NOW - timedelta(hours=1))
    res = advance_campaign(session, c, now=NOW)
    assert res["action"] == ACTION_WAIT
    assert called == []


def test_advance_sync_finds_nonresponders(session, monkeypatch):
    def fake_sync(campaign_id, sess):
        camp = sess.get(Campaign, campaign_id)
        camp.status = "outbound_ready"  # sync found people to call
        sess.add(camp); sess.commit()
    monkeypatch.setattr(orch, "sync_campaign_survey_responses_for_campaign", fake_sync)

    c = persist_campaign(session, status="survey_sent",
                         survey_sent_at=NOW - timedelta(hours=25))
    res = advance_campaign(session, c, now=NOW)
    assert res["action"] == ACTION_SYNC_RESPONSES
    assert c.status == "outbound_ready"


def test_advance_sync_all_responded_completes(session, monkeypatch):
    # Sync leaves status at survey_sent -> nobody to call -> campaign completes.
    monkeypatch.setattr(orch, "sync_campaign_survey_responses_for_campaign",
                        lambda campaign_id, sess: None)
    c = persist_campaign(session, status="survey_sent",
                         survey_sent_at=NOW - timedelta(hours=25))
    advance_campaign(session, c, now=NOW)
    assert c.status == "completed"


def test_advance_build_queue_with_attempts(session, monkeypatch):
    monkeypatch.setattr(orch, "build_outbound_call_queue",
                        lambda campaign_id, sess: [object()])  # one attempt queued
    c = persist_campaign(session, status="outbound_ready")
    res = advance_campaign(session, c, now=NOW)
    assert res["action"] == ACTION_BUILD_QUEUE
    assert res["queued"] == 1
    assert c.status != "completed"


def test_advance_build_queue_empty_completes(session, monkeypatch):
    monkeypatch.setattr(orch, "build_outbound_call_queue",
                        lambda campaign_id, sess: [])  # nobody eligible
    c = persist_campaign(session, status="outbound_ready")
    advance_campaign(session, c, now=NOW)
    assert c.status == "completed"


def test_advance_drain_respects_concurrency(session, monkeypatch):
    calls = []
    monkeypatch.setattr(orch, "execute_next_outbound_call_for_campaign",
                        lambda campaign_id, sess: calls.append(1) or {"status": "calling"})
    c = persist_campaign(session, status="outbound_calling")
    for _ in range(3):
        add_attempt(session, c.id, "queued")

    res = advance_campaign(session, c, now=NOW, concurrency=2)
    assert res["action"] == ACTION_DRAIN_CALLS
    assert res["placed"] == 2          # only 2 free slots this tick
    assert len(calls) == 2


def test_advance_drain_stops_when_queue_empty(session, monkeypatch):
    seq = [{"status": "calling"}, {"status": "no_queued_attempt"}]
    monkeypatch.setattr(orch, "execute_next_outbound_call_for_campaign",
                        lambda campaign_id, sess: seq.pop(0))
    c = persist_campaign(session, status="outbound_calling")
    add_attempt(session, c.id, "queued")

    res = advance_campaign(session, c, now=NOW, concurrency=5)
    assert res["placed"] == 1          # stopped when the queue ran dry


def test_advance_outbound_calling_completes_when_empty(session):
    c = persist_campaign(session, status="outbound_calling")  # no attempts at all
    res = advance_campaign(session, c, now=NOW)
    assert res["action"] == ACTION_COMPLETE
    assert c.status == "completed"


def _add_candidate_with_attempt(session, campaign_id, *, attempt_number, status):
    cand = CampaignCandidate(
        campaign_id=campaign_id,
        candidate_name="Retry Me",
        phone="+2348010000000",
        survey_status="non_responder",
        call_status=status,
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    session.add(OutboundCallAttempt(
        campaign_id=campaign_id, candidate_id=cand.id, phone="+2348010000000",
        attempt_number=attempt_number, status=status,
    ))
    session.commit()
    return cand


def test_advance_auto_retries_before_completing(session):
    # Queue is drained, but a candidate's last attempt was a retryable
    # no_answer under the cap -> orchestrator requeues instead of completing.
    c = persist_campaign(session, status="outbound_calling")
    _add_candidate_with_attempt(session, c.id, attempt_number=1, status="no_answer")

    res = advance_campaign(session, c, now=NOW, max_attempts=3)
    assert res["action"] == ACTION_RETRY_QUEUE
    assert res["queued"] == 1
    assert c.status == "outbound_calling"


def test_advance_completes_when_retries_exhausted(session):
    c = persist_campaign(session, status="outbound_calling")
    _add_candidate_with_attempt(session, c.id, attempt_number=3, status="no_answer")

    res = advance_campaign(session, c, now=NOW, max_attempts=3)
    assert res["action"] == ACTION_COMPLETE
    assert c.status == "completed"


def test_advance_no_autoretry_when_cap_is_one(session):
    # max_attempts <= 1 disables auto-retry entirely.
    c = persist_campaign(session, status="outbound_calling")
    _add_candidate_with_attempt(session, c.id, attempt_number=1, status="no_answer")

    res = advance_campaign(session, c, now=NOW, max_attempts=1)
    assert res["action"] == ACTION_COMPLETE
    assert c.status == "completed"


def test_advance_no_retry_for_reached_candidate(session):
    # A candidate who was reached (answered) is never re-called.
    c = persist_campaign(session, status="outbound_calling")
    _add_candidate_with_attempt(session, c.id, attempt_number=1, status="answered")

    res = advance_campaign(session, c, now=NOW, max_attempts=3)
    assert res["action"] == ACTION_COMPLETE
    assert c.status == "completed"


def test_advance_draft_is_noop(session, monkeypatch):
    called = []
    monkeypatch.setattr(orch, "sync_campaign_survey_responses_for_campaign",
                        lambda *a, **k: called.append("sync"))
    c = persist_campaign(session, status="draft")
    res = advance_campaign(session, c, now=NOW)
    assert res["action"] == ACTION_NONE
    assert called == []


# --- Part 3: the job picks up only active campaigns ------------------------

def test_orchestration_job_processes_only_active_campaigns(session, monkeypatch):
    from app.jobs import campaign_orchestration_jobs as orch_jobs

    # Point the job's own Session at the in-memory test DB.
    monkeypatch.setattr(orch_jobs, "engine", session.get_bind())
    seen = []
    monkeypatch.setattr(orch_jobs, "advance_campaign",
                        lambda sess, campaign, **kw: seen.append(campaign.status) or {"action": "wait"})

    for status in ("survey_sent", "waiting_for_responses", "outbound_ready", "outbound_calling"):
        persist_campaign(session, status=status, survey_sent_at=NOW)
    for status in ("draft", "uploaded", "completed", "failed", "survey_sending",
                   "non_response_checking"):
        persist_campaign(session, status=status)

    result = orch_jobs.run_campaign_orchestration_job()

    assert result["campaigns"] == 4
    assert sorted(seen) == sorted(
        ["survey_sent", "waiting_for_responses", "outbound_ready", "outbound_calling"]
    )
