"""Large campaigns must not silently strand candidates.

The queue builders page through eligible non-responders; a campaign bigger than
one page must still get every candidate queued (and, later, retried) rather
than quietly stopping at the page boundary.
"""

from sqlmodel import select

from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_call_attempt import OutboundCallAttempt
from app.services.campaign_service import (
    build_outbound_call_queue,
    build_outbound_retry_queue,
)

# One more than the 500-row page size used when reading eligible candidates.
OVER_ONE_PAGE = 505


def _campaign_with_non_responders(session, count):
    campaign = Campaign(name="Mass Intake", status="outbound_ready")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    session.add_all(
        [
            CampaignCandidate(
                campaign_id=campaign.id,
                candidate_name=f"Candidate {i}",
                phone=f"+23480100{i:05d}",
                survey_status="non_responder",
                call_status="not_queued",
            )
            for i in range(count)
        ]
    )
    session.commit()
    return campaign


def test_build_queue_covers_every_candidate_past_one_page(session):
    campaign = _campaign_with_non_responders(session, OVER_ONE_PAGE)

    attempts = build_outbound_call_queue(campaign.id, session)

    assert len(attempts) == OVER_ONE_PAGE
    stored = session.exec(
        select(OutboundCallAttempt).where(
            OutboundCallAttempt.campaign_id == campaign.id
        )
    ).all()
    assert len(stored) == OVER_ONE_PAGE
    # Nobody is left un-queued.
    unqueued = session.exec(
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign.id)
        .where(CampaignCandidate.call_status == "not_queued")
    ).all()
    assert unqueued == []


def test_build_queue_is_idempotent_across_pages(session):
    campaign = _campaign_with_non_responders(session, OVER_ONE_PAGE)

    build_outbound_call_queue(campaign.id, session)
    second = build_outbound_call_queue(campaign.id, session)

    # Re-running reuses the existing attempts rather than duplicating them.
    assert len(second) == OVER_ONE_PAGE
    stored = session.exec(
        select(OutboundCallAttempt).where(
            OutboundCallAttempt.campaign_id == campaign.id
        )
    ).all()
    assert len(stored) == OVER_ONE_PAGE


def test_retry_queue_covers_every_candidate_past_one_page(session):
    campaign = _campaign_with_non_responders(session, OVER_ONE_PAGE)
    build_outbound_call_queue(campaign.id, session)

    # Every first attempt went unanswered.
    for attempt in session.exec(
        select(OutboundCallAttempt).where(
            OutboundCallAttempt.campaign_id == campaign.id
        )
    ).all():
        attempt.status = "no_answer"
        session.add(attempt)
    session.commit()

    retries = build_outbound_retry_queue(campaign.id, session, max_attempts=3)

    assert len(retries) == OVER_ONE_PAGE
    assert all(a.attempt_number == 2 for a in retries)
