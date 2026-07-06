
from fastapi import APIRouter, Depends, Query, HTTPException

from app.schemas.campaign import CampaignRead, CampaignCreate, CampaignCandidateRead, CampaignCandidateCreate, CampaignCandidateSurveyStatusUpdate, OutboundCallAttemptRead
from sqlmodel import Session
from app.services.campaign_service import (
    create_campaign as create_campaign_service,
    list_campaigns as list_campaigns_service,
    get_campaign_by_id,
    add_campaign_candidates,
    list_campaign_candidates as list_campaign_candidates_service,
    get_campaign_candidate_by_id,
    update_campaign_candidate_survey_status,
    list_eligible_non_responders as list_eligible_non_responders_service,
    build_outbound_call_queue as build_outbound_call_queue_service,
   list_outbound_call_attempts as list_outbound_call_attempts_service,
    )

from app.core.database import get_session



router = APIRouter(prefix="/campaigns", tags=["Campaigns"])



@router.post("", response_model=CampaignRead, status_code=201)
def create_campaign(
    campaign: CampaignCreate,
    session: Session = Depends(get_session)
):
    return create_campaign_service(campaign, session)



@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    
    return list_campaigns_service(session, limit, offset=offset)

@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_session)
):
    
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign is not found")
    
    return campaign


@router.post("/{campaign_id}/candidates", response_model=list[CampaignCandidateRead], status_code=201)
def add_candidates_to_campaign(
    campaign_id: str,
    candidates: list[CampaignCandidateCreate],
    session: Session = Depends(get_session)
):
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
         raise HTTPException(status_code=404, detail="Campaign not found")
    
    return add_campaign_candidates(campaign_id, candidates, session)


@router.get("/{campaign_id}/candidates", response_model=list[CampaignCandidateRead])
def list_campaign_candidates(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return list_campaign_candidates_service(
        campaign_id,
        session,
        limit=limit,
        offset=offset
    )



@router.patch("/{campaign_id}/candidates/{candidate_id}/survey-status", response_model=CampaignCandidateRead)
def update_candidate_survey_status(
    campaign_id: str,
    candidate_id: str,
    status_update: CampaignCandidateSurveyStatusUpdate,
    session: Session = Depends(get_session),
):
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    candidate = get_campaign_candidate_by_id(campaign_id, candidate_id, session)

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    return update_campaign_candidate_survey_status(
        candidate,
        status_update,
        session
    )


@router.get("/{campaign_id}/non-responders", response_model=list[CampaignCandidateRead])
def list_eligible_non_responders(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return list_eligible_non_responders_service(
       campaign_id,
       session,
       limit=limit,
       offset=offset
    )




@router.post("/{campaign_id}/outbound/build-queue", response_model=list[OutboundCallAttemptRead], status_code=201)
def build_outbound_queue(
    campaign_id: str,
    session: Session = Depends(get_session),
):
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return build_outbound_call_queue_service(campaign_id, session)


@router.get("/{campaign_id}/outbound/attempts", response_model=list[OutboundCallAttemptRead])
def list_outbound_attempts(
    campaign_id: str,
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return list_outbound_call_attempts_service(
        campaign_id,
        session,
        limit=limit,
        offset=offset,
    )