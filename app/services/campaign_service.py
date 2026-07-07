
from sqlmodel import Session, select
from datetime import datetime
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.schemas.campaign import CampaignCandidateCreate, CampaignCreate, CampaignCandidateSurveyStatusUpdate
from app.models.outbound_call_attempt import OutboundCallAttempt


def parse_optional_datetime(value):
    if value is None or isinstance(value, datetime):
        return value

    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def create_campaign(campaign_data: CampaignCreate, session: Session):

    campaign = Campaign(
        name=campaign_data.name,
        tool_name=campaign_data.tool_name,
        survey_id=campaign_data.survey_id,
        surveymonkey_collector_id=campaign_data.surveymonkey_collector_id,
        response_wait_hours=campaign_data.response_wait_hours
    )

    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    return campaign



def get_campaign_by_id(campaign_id: str, session: Session) -> Campaign | None:
    return session.get(Campaign, campaign_id)


def list_campaigns(session: Session, limit: int = 50, offset: int = 0) -> list[Campaign]:
    statement = (
        select(Campaign)
        .order_by(Campaign.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()





def add_campaign_candidates(
        campaign_id: str,
        candidates_data: list[CampaignCandidateCreate],
        session: Session
) -> list[CampaignCandidate]:
    candidates = []

    for candidate_data in candidates_data:
        candidate = CampaignCandidate(
            campaign_id=campaign_id,
            candidate_name=candidate_data.candidate_name,
            email=str(candidate_data.email) if candidate_data.email else None,
            phone=candidate_data.phone,
            tool_name=candidate_data.tool_name,
            campaign_name=candidate_data.campaign_name,
            external_candidate_id=candidate_data.external_candidate_id,
            opted_out_call=candidate_data.opted_out_call,
            opted_out_email=candidate_data.opted_out_email,
        )

        candidates.append(candidate)

    session.add_all(candidates)
    session.commit()


    for candidate in candidates:
        session.refresh(candidate)


    return candidates
    


def list_campaign_candidates(
        campaign_id: str,
        session: Session,
        limit: int = 100,
        offset: int = 0,
) -> list[CampaignCandidate]:
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .order_by(CampaignCandidate.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    
    return session.exec(statement).all()




def get_campaign_candidate_by_id(
        campaign_id: str,
        candidate_id: str,
        session: Session,
) -> CampaignCandidate | None:
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .where(CampaignCandidate.id == candidate_id)
    )

    return session.exec(statement).first()



def update_campaign_candidate_survey_status(
    candidate: CampaignCandidate,
    status_update: CampaignCandidateSurveyStatusUpdate,
    session: Session
) -> CampaignCandidate:
    candidate.survey_status = status_update.survey_status

    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    return candidate



def list_eligible_non_responders(
        campaign_id: str,
        session: Session,
        limit: int = 100,
        offset: int = 0
) -> list[CampaignCandidate]:
    statement = (
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_id == campaign_id)
        .where(CampaignCandidate.survey_status == "non_responder")
        .where(CampaignCandidate.opted_out_call.is_(False))
        .where(CampaignCandidate.phone.is_not(None))
        .where(CampaignCandidate.phone != "")
        .order_by(CampaignCandidate.created_at.asc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()





def get_existing_outbound_attempt_for_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session,
) -> OutboundCallAttempt | None:
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .where(OutboundCallAttempt.candidate_id == candidate_id)
    )

    return session.exec(statement).first()


def build_outbound_call_queue(
    campaign_id: str,
    session: Session,
) -> list[OutboundCallAttempt]:
    eligible_candidates = list_eligible_non_responders(
        campaign_id=campaign_id,
        session=session,
        limit=500,
        offset=0,
    )

    attempts: list[OutboundCallAttempt] = []

    for candidate in eligible_candidates:
        existing_attempt = get_existing_outbound_attempt_for_candidate(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            session=session,
        )

        if existing_attempt:
            attempts.append(existing_attempt)
            continue

        attempt = OutboundCallAttempt(
            campaign_id=campaign_id,
            candidate_id=candidate.id,
            candidate_name=candidate.candidate_name,
            candidate_email=candidate.email,
            campaign_name=candidate.campaign_name,
            tool_name=candidate.tool_name,
            phone=candidate.phone,
            attempt_number=1,
            status="queued",
        )
        attempts.append(attempt)
        session.add(attempt)

        candidate.call_status = "queued"
        session.add(candidate)

    session.commit()

    for attempt in attempts:
        session.refresh(attempt)

    return attempts


def list_outbound_call_attempts(
    campaign_id: str,
    session: Session,
    limit: int = 100,
    offset: int = 0,
) -> list[OutboundCallAttempt]:
    statement = (
        select(OutboundCallAttempt)
        .where(OutboundCallAttempt.campaign_id == campaign_id)
        .order_by(OutboundCallAttempt.created_at.asc())
        .limit(limit)
        .offset(offset)
    )

    return session.exec(statement).all()



def apply_candidate_survey_updates(
        campaign_id: str,
        updates: list[dict],
        session: Session
) -> list[CampaignCandidate]:
    updated_candidates: list[CampaignCandidate] = []

    for update in updates:
        candidate = get_campaign_candidate_by_id(
            campaign_id=campaign_id,
            candidate_id=update["candidate_id"],
            session=session
        )


        if not candidate:
            continue

        candidate.surveymonkey_recipient_id = update["surveymonkey_recipient_id"]
        candidate.surveymonkey_response_id = update["surveymonkey_response_id"]
        candidate.surveymonkey_response_status = update["surveymonkey_response_status"]
        candidate.survey_status = update["survey_status"]
        candidate.survey_responded_at = parse_optional_datetime(update["survey_responded_at"])
        session.add(candidate)
        updated_candidates.append(candidate)

    session.commit()

    for candidate in updated_candidates:
        session.refresh(candidate)

    
    return updated_candidates
