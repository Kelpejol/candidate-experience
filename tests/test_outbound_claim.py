"""Concurrency-safety of claiming the next queued outbound attempt.

The claim must be atomic so two workers never dial the same candidate twice.
"""

from sqlalchemy import update

import app.services.campaign_service as cs
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services.campaign_service import claim_next_queued_outbound_attempt


def _campaign_with_attempts(session, statuses):
    campaign = Campaign(name="C", status="outbound_calling")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    attempts = []
    for i, status in enumerate(statuses):
        candidate = CampaignCandidate(
            campaign_id=campaign.id,
            candidate_name=f"n{i}",
            phone=f"+234801000000{i}",
            survey_status="non_responder",
            call_status=status,
        )
        session.add(candidate)
        session.commit()
        session.refresh(candidate)
        attempt = OutboundCallAttempt(
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            phone=candidate.phone,
            attempt_number=1,
            status=status,
        )
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        attempts.append(attempt)
    return campaign, attempts


def test_claim_flips_queued_to_calling(session):
    campaign, (attempt,) = _campaign_with_attempts(session, ["queued"])
    claimed = claim_next_queued_outbound_attempt(campaign.id, session)
    assert claimed is not None and claimed.id == attempt.id
    assert claimed.status == "calling"
    assert claimed.started_at is not None


def test_claim_skips_already_claimed(session):
    # An attempt already "calling" is never re-claimed (WHERE status='queued').
    campaign, _ = _campaign_with_attempts(session, ["calling"])
    assert claim_next_queued_outbound_attempt(campaign.id, session) is None


def test_claim_returns_each_queued_once(session):
    campaign, attempts = _campaign_with_attempts(session, ["queued", "queued"])
    first = claim_next_queued_outbound_attempt(campaign.id, session)
    second = claim_next_queued_outbound_attempt(campaign.id, session)
    third = claim_next_queued_outbound_attempt(campaign.id, session)

    assert {first.id, second.id} == {a.id for a in attempts}
    assert third is None  # both claimed; nothing queued remains


def test_claim_loser_advances_to_next(session, monkeypatch):
    # Simulate losing the race: the first select hands back a row that another
    # worker has already flipped to "calling". The conditional UPDATE matches 0
    # rows, so the claim must move on to the next genuinely-queued attempt.
    campaign, (a0, a1) = _campaign_with_attempts(session, ["queued", "queued"])
    real_next = cs.get_next_queued_outbound_attempt
    calls = {"n": 0}

    def fake_next(campaign_id, session):
        calls["n"] += 1
        if calls["n"] == 1:
            session.execute(
                update(OutboundCallAttempt)
                .where(OutboundCallAttempt.id == a0.id)
                .values(status="calling")
            )
            session.commit()
            return session.get(OutboundCallAttempt, a0.id)
        return real_next(campaign_id, session)

    monkeypatch.setattr(cs, "get_next_queued_outbound_attempt", fake_next)

    claimed = cs.claim_next_queued_outbound_attempt(campaign.id, session)
    assert claimed.id == a1.id
