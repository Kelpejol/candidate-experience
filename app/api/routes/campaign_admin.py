"""Staff/dashboard endpoints for the inbound-IVR side of a campaign:
toggle inbound availability + its window, point it at a KB source, and
trigger a (re-)index of that campaign's KB.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import get_settings
from app.core.database import get_session
from app.integrations.sharepoint_client import build_sharepoint_client
from app.schemas.campaign import CampaignOutboundUpdate, CampaignRead
from app.services.campaign_scope_service import campaign_scope
from app.services.campaign_service import get_campaign_by_id
from app.services.sharepoint_kb_loader import load_sharepoint_kb_folder
from app.services.voice_kb_ingest import load_local_kb_dir, reindex_campaign

router = APIRouter(prefix="/campaigns", tags=["Campaign Admin"])


class CampaignInboundUpdate(BaseModel):
    inbound_active: bool | None = None
    active_from: datetime | None = None
    active_until: datetime | None = None
    kb_source: str | None = None
    kb_scope: str | None = None


@router.patch("/{campaign_id}/inbound")
def update_campaign_inbound(
    campaign_id: str,
    payload: CampaignInboundUpdate,
    session: Session = Depends(get_session),
):
    """Update a campaign's inbound settings. Only fields actually sent are
    changed (so sending `active_from: null` clears it, omitting it leaves it)."""
    campaign = get_campaign_by_id(campaign_id, session)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


@router.patch("/{campaign_id}/outbound-settings", response_model=CampaignRead)
def update_campaign_outbound_settings(
    campaign_id: str,
    payload: CampaignOutboundUpdate,
    session: Session = Depends(get_session),
):
    """Update a campaign's outbound call context (reason, org, assessment
    date/time, location, links, contact). Only the fields sent are changed."""
    campaign = get_campaign_by_id(campaign_id, session)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/kb/reindex")
def reindex_campaign_kb(campaign_id: str, session: Session = Depends(get_session)):
    """(Re-)index this campaign's KB from its configured source.

    Atomic: a source/embedding failure leaves the current KB untouched.
    """
    campaign = get_campaign_by_id(campaign_id, session)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not campaign.kb_source:
        raise HTTPException(status_code=400, detail="Campaign has no kb_source set")

    try:
        documents = load_local_kb_dir(campaign.kb_source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scope = campaign_scope(campaign)
    count = reindex_campaign(scope, documents)
    return {"campaign_id": campaign_id, "scope": scope, "reindexed": count}


@router.post("/{campaign_id}/kb/reindex-from-sharepoint")
def reindex_campaign_kb_from_sharepoint(
    campaign_id: str, session: Session = Depends(get_session)
):
    """(Re-)index this campaign's KB from a SharePoint folder.

    Unlike /kb/reindex (which treats kb_source as a LOCAL directory — the
    dev/demo path), this treats kb_source as the name of a folder inside the
    shared SharePoint site's document library, and pulls real .docx files
    from there via Microsoft Graph. Kept as a separate endpoint rather than
    auto-detecting from the kb_source string, which would be ambiguous — a
    local path and a SharePoint folder name can look identical.

    Non-.docx files and malformed headings inside a file are skipped with a
    warning rather than failing the whole batch; `warnings` in the response
    lists everything skipped and why, for whoever authored the SharePoint
    content to go fix. Atomic like the local path: a failure here leaves the
    current KB untouched (see voice_kb_ingest.reindex_campaign).
    """
    campaign = get_campaign_by_id(campaign_id, session)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not campaign.kb_source:
        raise HTTPException(status_code=400, detail="Campaign has no kb_source set")

    settings = get_settings()
    try:
        client = build_sharepoint_client(settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        documents, warnings = load_sharepoint_kb_folder(
            client,
            hostname=settings.sharepoint_hostname,
            site_path=settings.sharepoint_site_path,
            folder_path=campaign.kb_source,
            library_name=settings.sharepoint_library_name,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"SharePoint request failed: {exc}"
        ) from exc

    if not documents:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No usable .docx content found in SharePoint folder "
                f"{campaign.kb_source!r}. Warnings: {warnings}"
            ),
        )

    scope = campaign_scope(campaign)
    count = reindex_campaign(scope, documents)
    return {
        "campaign_id": campaign_id,
        "scope": scope,
        "reindexed": count,
        "warnings": warnings,
    }
