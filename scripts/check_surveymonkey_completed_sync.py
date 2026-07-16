"""Verify that a completed real SurveyMonkey invite syncs into local records.

This is intentionally read-only against SurveyMonkey: it only lists collector
recipients and survey responses. The only writes are to the local SQLite app
database, where it creates/reuses a small TEST campaign and candidate so the
normal sync service can be exercised end to end.

Run with:
    PYTHONPATH=. .venv/bin/python scripts/check_surveymonkey_completed_sync.py \
        --survey-id 423001176 \
        --collector-id 440321814 \
        --email paul@dragnet-solutions.com
"""

import argparse
from datetime import datetime

from sqlmodel import Session, select

from app.core.database import create_db_and_tables, engine
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.services.surveymonkey_sync_service import (
    sync_campaign_survey_responses_for_campaign,
)


parser = argparse.ArgumentParser()
parser.add_argument("--survey-id", required=True)
parser.add_argument("--collector-id", required=True)
parser.add_argument("--email", required=True)
parser.add_argument("--phone", default="+2348010000000")
parser.add_argument("--campaign-name", default="TEST - SurveyMonkey Completed Sync Check")
args = parser.parse_args()


def get_or_create_test_campaign(session: Session) -> Campaign:
    campaign = session.exec(
        select(Campaign).where(Campaign.name == args.campaign_name)
    ).first()

    if not campaign:
        campaign = Campaign(
            name=args.campaign_name,
            tool_name="FOT",
            status="survey_sent",
            survey_sent_at=datetime.utcnow(),
        )

    campaign.survey_id = args.survey_id
    campaign.surveymonkey_collector_id = args.collector_id
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


def get_or_create_test_candidate(session: Session, campaign: Campaign) -> CampaignCandidate:
    candidate = session.exec(
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign.id)
        .where(CampaignCandidate.email == args.email)
    ).first()

    if not candidate:
        candidate = CampaignCandidate(
            campaign_id=campaign.id,
            candidate_name="SurveyMonkey Completed Sync Tester",
            email=args.email,
            phone=args.phone,
            tool_name=campaign.tool_name,
            campaign_name=campaign.name,
        )

    candidate.survey_status = "sent"
    candidate.call_status = "not_queued"
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


create_db_and_tables()

with Session(engine) as session:
    campaign = get_or_create_test_campaign(session)
    candidate = get_or_create_test_candidate(session, campaign)

    summary = sync_campaign_survey_responses_for_campaign(
        campaign_id=campaign.id,
        session=session,
    )
    session.refresh(candidate)
    session.refresh(campaign)

    print("SurveyMonkey completed-sync check finished")
    print(f"campaign_id: {campaign.id}")
    print(f"survey_id: {campaign.survey_id}")
    print(f"collector_id: {campaign.surveymonkey_collector_id}")
    print(f"candidate_id: {candidate.id}")
    print(f"candidate_email: {candidate.email}")
    print(f"candidate_survey_status: {candidate.survey_status}")
    print(f"surveymonkey_recipient_id: {candidate.surveymonkey_recipient_id}")
    print(f"surveymonkey_response_id: {candidate.surveymonkey_response_id}")
    print(f"surveymonkey_response_status: {candidate.surveymonkey_response_status}")
    print(f"survey_responded_at: {candidate.survey_responded_at}")
    print(f"campaign_status: {campaign.status}")
    print(f"sync_summary: {summary}")
