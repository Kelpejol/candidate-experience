"""SurveyMonkey campaign distribution helpers.

Prepares campaign candidates as SurveyMonkey message recipients and maps
returned recipient ids back onto local CampaignCandidate rows. Sending is a
separate operation and is intentionally not performed here.
"""

from datetime import datetime

from sqlmodel import Session

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.models.campaign import Campaign
from app.models.campaign_candidate import CampaignCandidate
from app.services.campaign_service import list_campaign_candidates


SURVEY_SEND_CONFIRMATION = "SEND_SURVEY_EMAILS"

# Once a campaign reaches any of these, a send has already happened (or is in
# flight) for it. Blocking a second send here is what makes send_campaign_
# survey_message at-most-once: without it, a retried request after a timeout,
# or an officer re-clicking Send, would email every prepared candidate again.
_ALREADY_SENT_OR_IN_FLIGHT_STATUSES = {
    "survey_sending",
    "survey_sent",
    "waiting_for_responses",
    "non_response_checking",
    "outbound_ready",
    "outbound_calling",
    "completed",
}


def split_candidate_name(candidate_name: str | None) -> tuple[str, str]:
    if not candidate_name:
        return "", ""

    parts = candidate_name.strip().split(maxsplit=1)

    if len(parts) == 1:
        return parts[0], ""

    return parts[0], parts[1]


def build_surveymonkey_contact_payload(candidate: CampaignCandidate) -> dict:
    return {"email": candidate.email}


def list_email_eligible_candidates(
    campaign_id: str,
    session: Session,
) -> list[CampaignCandidate]:
    candidates = list_campaign_candidates(
        campaign_id=campaign_id,
        session=session,
        limit=500,
        offset=0,
    )

    return [
        candidate
        for candidate in candidates
        if candidate.email
        and not candidate.opted_out_email
        and candidate.survey_status == "not_sent"
    ]


def extract_surveymonkey_recipients(payload: dict) -> list[dict]:
    recipients = []

    if isinstance(payload.get("succeeded"), list):
        recipients.extend(payload["succeeded"])

    if isinstance(payload.get("existing"), list):
        recipients.extend(payload["existing"])

    if recipients:
        return recipients

    if isinstance(payload.get("data"), list):
        return payload["data"]

    if isinstance(payload.get("recipients"), list):
        return payload["recipients"]

    if isinstance(payload.get("contacts"), list):
        return payload["contacts"]

    return []


def extract_surveymonkey_id(payload: dict, label: str) -> str:
    value = payload.get("id") or payload.get(f"{label}_id")

    if not value and isinstance(payload.get("data"), dict):
        value = payload["data"].get("id") or payload["data"].get(f"{label}_id")

    if not value:
        raise RuntimeError(f"SurveyMonkey did not return a {label} id")

    return str(value)


def create_campaign_survey_message(
    *,
    campaign: Campaign,
    subject: str,
    body: str | None,
    session: Session,
    collector_id: str | None = None,
    collector_name: str | None = None,
) -> dict:
    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    if not campaign.survey_id:
        raise ValueError("Campaign does not have a SurveyMonkey survey configured")

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )
    resolved_collector_id = collector_id or campaign.surveymonkey_collector_id

    if not resolved_collector_id:
        collector = client.create_email_collector(
            survey_id=campaign.survey_id,
            name=collector_name or f"{campaign.name} Email Collector",
        )
        resolved_collector_id = extract_surveymonkey_id(collector, "collector")

    message = client.create_collector_message(
        collector_id=resolved_collector_id,
        subject=subject,
        body=body,
    )
    message_id = extract_surveymonkey_id(message, "message")

    if campaign.surveymonkey_collector_id != resolved_collector_id:
        campaign.surveymonkey_collector_id = resolved_collector_id
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

    return {
        "campaign_id": campaign.id,
        "survey_id": campaign.survey_id,
        "collector_id": resolved_collector_id,
        "message_id": message_id,
        "subject": subject,
    }


def apply_prepared_recipient_mapping(
    *,
    campaign: Campaign,
    candidates: list[CampaignCandidate],
    recipients: list[dict],
    session: Session,
) -> int:
    candidates_by_email = {
        candidate.email.lower(): candidate
        for candidate in candidates
        if candidate.email
    }
    updated_count = 0

    for recipient in recipients:
        email = recipient.get("email")

        if not email:
            continue

        candidate = candidates_by_email.get(str(email).lower())

        if not candidate:
            continue

        candidate.surveymonkey_recipient_id = str(
            recipient.get("id") or recipient.get("recipient_id") or ""
        ) or None
        session.add(candidate)
        updated_count += 1

    session.commit()

    for candidate in candidates:
        session.refresh(candidate)

    return updated_count


def mark_prepared_candidates_as_sent(
    *,
    campaign: Campaign,
    session: Session,
) -> int:
    candidates = list_campaign_candidates(
        campaign_id=campaign.id,
        session=session,
        limit=500,
        offset=0,
    )
    updated_count = 0

    for candidate in candidates:
        if not candidate.surveymonkey_recipient_id:
            continue

        if candidate.survey_status != "not_sent":
            continue

        candidate.survey_status = "sent"
        session.add(candidate)
        updated_count += 1

    campaign.status = "survey_sent"

    if campaign.survey_sent_at is None:
        campaign.survey_sent_at = datetime.utcnow()

    session.add(campaign)
    session.commit()

    for candidate in candidates:
        session.refresh(candidate)

    session.refresh(campaign)

    return updated_count


def send_campaign_survey_message(
    *,
    campaign: Campaign,
    message_id: str,
    confirm_send: str,
    session: Session,
    collector_id: str | None = None,
) -> dict:
    """Send a prepared SurveyMonkey message and mark candidates sent.

    At-most-once, not at-least-once: real survey emails should never go out
    twice, even if this call is retried or the process crashes mid-send. Two
    things make that true —

    1. A campaign already past "uploaded" (sending, sent, or beyond) refuses a
       second send outright — a retried request or a re-clicked Send button
       can't re-email everyone.
    2. The campaign is flipped to "survey_sending" and committed BEFORE the
       real SurveyMonkey call. A crash between that commit and the actual send
       leaves the campaign stuck at "survey_sending" rather than silently
       retryable — which is deliberate: we can't tell whether the email went
       out, so an operator resolves it explicitly (checking SurveyMonkey, then
       resetting the status via PATCH /campaigns/{id}/status) rather than the
       system guessing and possibly double-sending.
    """
    if confirm_send != SURVEY_SEND_CONFIRMATION:
        raise ValueError(f"confirm_send must be {SURVEY_SEND_CONFIRMATION}")

    if campaign.status in _ALREADY_SENT_OR_IN_FLIGHT_STATUSES:
        raise ValueError(
            f"Campaign is already {campaign.status} — refusing to send the "
            "survey again. If a previous send actually failed, confirm that "
            "with SurveyMonkey, then reset the campaign status before retrying."
        )

    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    resolved_collector_id = collector_id or campaign.surveymonkey_collector_id

    if not resolved_collector_id:
        raise ValueError("Campaign does not have a SurveyMonkey collector configured")

    # Claim the send before making it real. If the process dies right after
    # this commit, the campaign is left in "survey_sending" — not retryable by
    # a naive re-run — rather than "not_sent", which could send twice.
    campaign.status = "survey_sending"
    session.add(campaign)
    session.commit()

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )
    client.send_collector_message(
        collector_id=resolved_collector_id,
        message_id=message_id,
    )
    updated_candidates = mark_prepared_candidates_as_sent(
        campaign=campaign,
        session=session,
    )

    return {
        "campaign_id": campaign.id,
        "collector_id": resolved_collector_id,
        "message_id": message_id,
        "sent": True,
        "updated_candidates": updated_candidates,
    }


def prepare_campaign_survey_recipients(
    *,
    campaign: Campaign,
    message_id: str,
    session: Session,
    collector_id: str | None = None,
) -> dict:
    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    resolved_collector_id = collector_id or campaign.surveymonkey_collector_id

    if not resolved_collector_id:
        raise ValueError("Campaign does not have a SurveyMonkey collector configured")

    candidates = list_email_eligible_candidates(
        campaign_id=campaign.id,
        session=session,
    )
    contacts = [
        build_surveymonkey_contact_payload(candidate)
        for candidate in candidates
    ]
    total_candidates = len(
        list_campaign_candidates(
            campaign_id=campaign.id,
            session=session,
            limit=500,
            offset=0,
        )
    )

    if not contacts:
        return {
            "campaign_id": campaign.id,
            "collector_id": resolved_collector_id,
            "message_id": message_id,
            "eligible_candidates": 0,
            "skipped_candidates": total_candidates,
            "prepared_recipients": 0,
        }

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )
    response = client.add_message_recipients_bulk(
        collector_id=resolved_collector_id,
        message_id=message_id,
        contacts=contacts,
    )
    recipients = extract_surveymonkey_recipients(response)
    prepared_count = apply_prepared_recipient_mapping(
        campaign=campaign,
        candidates=candidates,
        recipients=recipients,
        session=session,
    )

    if campaign.surveymonkey_collector_id != resolved_collector_id:
        campaign.surveymonkey_collector_id = resolved_collector_id
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

    return {
        "campaign_id": campaign.id,
        "collector_id": resolved_collector_id,
        "message_id": message_id,
        "eligible_candidates": len(candidates),
        "skipped_candidates": total_candidates - len(candidates),
        "prepared_recipients": prepared_count,
    }
