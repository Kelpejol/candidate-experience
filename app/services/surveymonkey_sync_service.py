"""Syncs SurveyMonkey survey responses into campaign candidate records.

Pulls all recipients and responses for a campaign's configured SurveyMonkey
collector/survey, matches SurveyMonkey recipients to campaign candidates by
email, classifies each candidate as responded/partially responded/non-
responder, and persists that back onto the candidate rows.
"""

from sqlmodel import Session

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.services.campaign_service import (
    apply_candidate_survey_updates,
    get_campaign_by_id,
    list_campaign_candidates,
)

def summarize_collector_responses(
    recipients: list[dict],
    responses: list[dict],
    collector_id: str,
) -> dict:
    """Summarize a survey's responses for one collector into completed/partial/non-responder buckets.

    `responses` may span multiple collectors on the survey, so responses are
    first filtered down to `collector_id`. Builds recipient-id-keyed lookup
    maps of completed and partial responses, and derives which recipients in
    `recipients` never responded at all. Returns a dict with counts plus the
    `responded_by_recipient_id` / `non_responder_recipients` lookups consumed
    by build_candidate_survey_updates. Pure function, no side effects.
    """
    # IDs coming back from the API can be int or str depending on endpoint;
    # normalize both sides to str before comparing.
    responses_for_collector = [
        response
        for response in responses
        if str(response.get("collector_id")) == str(collector_id)
    ]

    completed_responses = [
        response
        for response in responses_for_collector
        if response.get("response_status") == "completed"
    ]

    partial_responses = [
        response
        for response in responses_for_collector
        if response.get("response_status") == "partial"
    ]

    # Completed responses listed first so that, in the unusual case a
    # recipient shows up in both lists, the dict comprehension below keeps
    # whichever came last (partial) — last-write-wins on duplicate keys.
    responded_by_recipient_id = {
        str(response.get("recipient_id")): response
        for response in completed_responses + partial_responses
        if response.get("recipient_id") is not None
    }

    completed_recipient_ids = {
        str(response.get("recipient_id"))
        for response in completed_responses
        if response.get("recipient_id") is not None
    }

    partial_recipient_ids = {
        str(response.get("recipient_id"))
        for response in partial_responses
        if response.get("recipient_id") is not None
    }

    non_responder_recipients = [
        recipient
        for recipient in recipients
        if str(recipient.get("id")) not in responded_by_recipient_id
    ]

    return {
        "total_recipients": len(recipients),
        "completed": len(completed_recipient_ids),
        "partial": len(partial_recipient_ids),
        "non_responders": len(non_responder_recipients),
        "responded_by_recipient_id": responded_by_recipient_id,
        "non_responder_recipients": non_responder_recipients,
    }


def build_candidate_survey_updates(
    candidates: list,
    recipients: list[dict],
    response_summary: dict,
) -> list[dict]:
    """Build per-candidate SurveyMonkey update payloads.

    Matches each candidate to a SurveyMonkey recipient by (lowercased) email,
    then uses `response_summary["responded_by_recipient_id"]` to classify the
    candidate as "responded", "partial_response", or "non_responder".
    Candidates with no email or no matching recipient are skipped. Returns a
    list of update dicts (candidate_id, recipient/response ids, status,
    responded_at) consumed by campaign_service.apply_candidate_survey_updates;
    this function itself has no side effects.
    """
    # SurveyMonkey recipient emails may differ in case from stored candidate
    # emails, so join on a lowercased email key.
    recipients_by_email = {
        str(recipient.get("email")).lower(): recipient
        for recipient in recipients
        if recipient.get("email")
    }

    responded_by_recipient_id = response_summary["responded_by_recipient_id"]

    updates = []

    for candidate in candidates:
        candidate_email = getattr(candidate, "email", None)
        if not candidate_email:
            continue

        recipient = recipients_by_email.get(candidate_email.lower())
        if not recipient:
            continue

        recipient_id = str(recipient.get("id"))
        response = responded_by_recipient_id.get(recipient_id)

        if response:
            response_status = response.get("response_status")
            survey_status = (
                "responded"
                if response_status == "completed"
                else "partial_response"
            )

            updates.append(
                {
                    "candidate_id": candidate.id,
                    "surveymonkey_recipient_id": recipient_id,
                    "surveymonkey_response_id": str(response.get("id")),
                    "surveymonkey_response_status": response_status,
                    "survey_status": survey_status,
                    "survey_responded_at": response.get("date_modified"),
                }
            )
        else:
            updates.append(
                {
                    "candidate_id": candidate.id,
                    "surveymonkey_recipient_id": recipient_id,
                    "surveymonkey_response_id": None,
                    "surveymonkey_response_status": None,
                    "survey_status": "non_responder",
                    "survey_responded_at": None,
                }
            )

    return updates



def list_all_campaign_candidates(
    campaign_id: str,
    session: Session,
    page_size: int = 500,
) -> list:
    """Fetch every candidate for a campaign, paging through list_campaign_candidates.

    Read-only helper that walks limit/offset pages until a page comes back
    empty or shorter than `page_size` (i.e. the last page), accumulating all
    results in memory. No database writes.
    """
    candidates = []
    offset = 0

    while True:
        page = list_campaign_candidates(
            campaign_id=campaign_id,
            session=session,
            limit=page_size,
            offset=offset,
        )

        if not page:
            break

        candidates.extend(page)

        # A short page means we've reached the end; stop instead of making
        # one more round-trip that would just return an empty page.
        if len(page) < page_size:
            break

        offset += page_size

    return candidates


def sync_campaign_survey_responses_for_campaign(
    campaign_id: str,
    session: Session,
) -> dict:
    """Sync SurveyMonkey responses for one campaign into its candidate records.

    Loads the campaign and validates it has a survey/collector configured and
    that a SurveyMonkey access token is available, then fetches all collector
    recipients and all survey responses (bulk, paginated) from SurveyMonkey,
    summarizes/matches them against campaign candidates by email, and writes
    the resulting survey status back via apply_candidate_survey_updates.

    Side effects: makes SurveyMonkey API calls and commits candidate/campaign
    updates to the database. Raises ValueError if the campaign or its
    SurveyMonkey survey/collector ids are missing, and RuntimeError if the
    SurveyMonkey access token is not configured. Returns a summary dict of
    response counts and how many candidates were updated.
    """
    campaign = get_campaign_by_id(campaign_id, session)

    if not campaign:
        raise ValueError("Campaign not found")

    if not campaign.survey_id or not campaign.surveymonkey_collector_id:
        raise ValueError("Campaign does not have SurveyMonkey survey and collector configured")

    settings = get_settings()

    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")

    client = SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )

    recipients = client.list_all_collector_recipients(
        campaign.surveymonkey_collector_id
    )
    responses = client.list_all_survey_responses_bulk(campaign.survey_id)

    summary = summarize_collector_responses(
        recipients=recipients,
        responses=responses,
        collector_id=campaign.surveymonkey_collector_id,
    )

    candidates = list_all_campaign_candidates(
        campaign_id=campaign_id,
        session=session,
    )

    updates = build_candidate_survey_updates(
        candidates=candidates,
        recipients=recipients,
        response_summary=summary,
    )

    updated_candidates = apply_candidate_survey_updates(
        campaign_id=campaign_id,
        updates=updates,
        session=session,
    )

    return {
        "total_recipients": summary["total_recipients"],
        "completed": summary["completed"],
        "partial": summary["partial"],
        "non_responders": summary["non_responders"],
        "updated_candidates": len(updated_candidates),
    }