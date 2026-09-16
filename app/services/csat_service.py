"""Post-call CSAT: create invitations, send them, and sync responses.

PSA Flow E — after an inbound support call, send a short satisfaction survey
via SurveyMonkey and record the outcome. Reuses the existing SurveyMonkey
client + distribution helpers (collectors, messages, recipients, bulk
responses); it invents no new SurveyMonkey mechanics.

Design:
- One CsatInvitation per source call (idempotent on call_record_id).
- Email is resolved from the candidate's phone against CampaignCandidate (our
  own "candidate profile" source). No email -> pending_contact_lookup.
- The CSAT survey + collector are a STANDING pair (configured once from the
  CSAT template); each send creates a fresh collector message for the batch of
  ready invitations.
"""

from datetime import datetime

from sqlmodel import Session, select

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.models.call_record import CallRecord
from app.models.campaign_candidate import CampaignCandidate
from app.models.csat_invitation import CsatInvitation
from app.services.surveymonkey_campaign_template_service import (
    clone_surveymonkey_survey_from_template,
)
from app.services.surveymonkey_distribution_service import (
    extract_surveymonkey_id,
    extract_surveymonkey_recipients,
)

# Inbound support calls that were actually handled are the only CSAT-eligible
# calls (a missed / failed / voicemail call has no support experience to rate).
CSAT_ELIGIBLE_DISPOSITIONS = {"answered_by_ai", "handed_off_to_human"}

CSAT_EMAIL_SUBJECT = "How was your support experience?"


def _client() -> SurveyMonkeyClient:
    settings = get_settings()
    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")
    return SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )


def _parse_sm_datetime(value) -> datetime | None:
    """Parse a SurveyMonkey ISO timestamp (e.g. '2026-06-20T16:05:07+00:00')."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Eligibility + email resolution --------------------------------------

def is_call_csat_eligible(call_record: CallRecord) -> bool:
    """Whether a call should get a CSAT: an inbound, actually-handled support call."""
    return (
        call_record.direction == "inbound"
        and call_record.disposition in CSAT_ELIGIBLE_DISPOSITIONS
    )


def _candidate_by_phone(session: Session, phone: str | None) -> CampaignCandidate | None:
    """Look up a candidate profile by phone (our stand-in for a profile service)."""
    if not phone:
        return None
    return session.exec(
        select(CampaignCandidate).where(CampaignCandidate.phone == phone)
    ).first()


# --- Invitation creation --------------------------------------------------

def create_csat_invitation_for_call(
    session: Session,
    call_record: CallRecord,
    email: str | None = None,
) -> CsatInvitation:
    """Create (idempotently) a CSAT invitation for a call, computing its status.

    - existing invitation for this call -> returned unchanged
    - no resolvable email                -> pending_contact_lookup
    - candidate opted out of email       -> skipped_opt_out
    - otherwise                          -> ready_to_send
    """
    existing = session.exec(
        select(CsatInvitation).where(
            CsatInvitation.call_record_id == call_record.id
        )
    ).first()
    if existing:
        return existing

    candidate = _candidate_by_phone(session, call_record.candidate_phone)
    resolved_email = email or (candidate.email if candidate else None)
    opted_out = bool(candidate and candidate.opted_out_email)

    if not resolved_email:
        status = "pending_contact_lookup"
    elif opted_out:
        status = "skipped_opt_out"
    else:
        status = "ready_to_send"

    settings = get_settings()
    invitation = CsatInvitation(
        call_record_id=call_record.id,
        candidate_email=resolved_email,
        candidate_phone=call_record.candidate_phone,
        surveymonkey_survey_id=settings.surveymonkey_csat_survey_id,
        surveymonkey_collector_id=settings.surveymonkey_csat_collector_id,
        status=status,
    )
    session.add(invitation)
    session.commit()
    session.refresh(invitation)
    return invitation


# --- Send ----------------------------------------------------------------

def send_pending_csat(session: Session, limit: int = 100) -> dict:
    """Send all ready_to_send CSAT invitations as one SurveyMonkey batch.

    Creates a fresh message under the standing CSAT collector, adds the
    invitations' emails as recipients, sends, then records each invitation's
    recipient/message ids and marks it sent. Raises if the CSAT survey/collector
    aren't configured.

    At-most-once: invitations are claimed into "sending" and committed BEFORE
    the real SurveyMonkey send, so a crash mid-batch leaves them there rather
    than back at "ready_to_send" — which would let the next scheduled run email
    the same people again. A batch stuck at "sending" needs a human to check
    SurveyMonkey and decide, the same way a stuck outbound call needs a human
    rather than an automatic re-dial.
    """
    settings = get_settings()
    survey_id = settings.surveymonkey_csat_survey_id
    collector_id = settings.surveymonkey_csat_collector_id
    if not survey_id or not collector_id:
        raise ValueError("CSAT survey_id and collector_id are not configured")

    invitations = session.exec(
        select(CsatInvitation)
        .where(CsatInvitation.status == "ready_to_send")
        .where(CsatInvitation.candidate_email.is_not(None))
        .limit(limit)
    ).all()

    if not invitations:
        return {"sent": 0}

    claimed_at = datetime.utcnow()
    for invitation in invitations:
        invitation.status = "sending"
        invitation.updated_at = claimed_at
        session.add(invitation)
    session.commit()

    client = _client()
    message = client.create_collector_message(
        collector_id=collector_id, subject=CSAT_EMAIL_SUBJECT
    )
    message_id = extract_surveymonkey_id(message, "message")

    contacts = [{"email": inv.candidate_email} for inv in invitations]
    response = client.add_message_recipients_bulk(
        collector_id=collector_id, message_id=message_id, contacts=contacts
    )
    recipients = extract_surveymonkey_recipients(response)
    recipient_id_by_email = {
        str(r.get("email")).lower(): str(r.get("id") or r.get("recipient_id") or "")
        for r in recipients
        if r.get("email")
    }

    client.send_collector_message(collector_id=collector_id, message_id=message_id)

    sent_at = datetime.utcnow()
    for invitation in invitations:
        invitation.surveymonkey_message_id = message_id
        invitation.surveymonkey_recipient_id = (
            recipient_id_by_email.get((invitation.candidate_email or "").lower()) or None
        )
        invitation.status = "sent"
        invitation.sent_at = sent_at
        invitation.updated_at = sent_at
        session.add(invitation)

    session.commit()
    return {"sent": len(invitations)}


# --- Response sync -------------------------------------------------------

def build_csat_response_updates(
    invitations: list[CsatInvitation],
    responses: list[dict],
    collector_id: str,
) -> list[dict]:
    """Pure matcher: pair sent invitations to responses by recipient id.

    Returns a list of {invitation_id, response_id, responded_at} for the
    invitations that now have a response. No I/O.
    """
    responses_by_recipient = {
        str(response.get("recipient_id")): response
        for response in responses
        if response.get("recipient_id") is not None
        and str(response.get("collector_id")) == str(collector_id)
    }

    updates = []
    for invitation in invitations:
        if not invitation.surveymonkey_recipient_id:
            continue
        response = responses_by_recipient.get(invitation.surveymonkey_recipient_id)
        if response:
            updates.append(
                {
                    "invitation_id": invitation.id,
                    "response_id": str(response.get("id")),
                    "responded_at": response.get("date_modified"),
                }
            )
    return updates


# Rating-style question families whose selected choice carries a numeric
# weight we can use as the satisfaction score (1-5 on the CSAT template).
CSAT_RATING_FAMILIES = {"matrix", "opinion_scale"}


def parse_csat_survey_structure(survey_details: dict) -> dict:
    """Pure: index a survey's questions into what we need to score a response.

    Returns:
      - score_choice_weights: {choice_id -> int} for choices under rating-style
        questions (only choices that carry a numeric weight; the Yes/No/Partially
        resolution question has weightless choices and is correctly excluded).
      - comment_question_ids: set of open-ended question ids (the free-text box).
    """
    score_choice_weights: dict[str, int] = {}
    comment_question_ids: set[str] = set()
    for page in survey_details.get("pages", []) or []:
        for question in page.get("questions", []) or []:
            family = question.get("family")
            question_id = str(question.get("id"))
            if family == "open_ended":
                comment_question_ids.add(question_id)
            elif family in CSAT_RATING_FAMILIES:
                answers = question.get("answers") or {}
                for choice in answers.get("choices", []) or []:
                    weight = choice.get("weight")
                    if weight is None or choice.get("id") is None:
                        continue
                    try:
                        score_choice_weights[str(choice.get("id"))] = int(weight)
                    except (TypeError, ValueError):
                        continue
    return {
        "score_choice_weights": score_choice_weights,
        "comment_question_ids": comment_question_ids,
    }


def extract_csat_score_and_comment(
    response: dict, structure: dict
) -> tuple[int | None, str | None]:
    """Pure: pull the satisfaction score + free-text comment out of a response.

    Score is the weight of the selected rating choice; comment is the first
    non-empty open-ended answer. Anything unrecognised yields None (we never
    guess a score). No I/O.
    """
    score_weights = structure.get("score_choice_weights", {})
    comment_question_ids = structure.get("comment_question_ids", set())
    score: int | None = None
    comment: str | None = None
    for page in response.get("pages", []) or []:
        for question in page.get("questions", []) or []:
            question_id = str(question.get("id"))
            for answer in question.get("answers", []) or []:
                choice_id = answer.get("choice_id")
                if score is None and choice_id is not None:
                    mapped = score_weights.get(str(choice_id))
                    if mapped is not None:
                        score = mapped
                if comment is None and question_id in comment_question_ids:
                    text = answer.get("text")
                    if text and str(text).strip():
                        comment = str(text).strip()
    return score, comment


def sync_csat_responses(session: Session) -> dict:
    """Fetch CSAT responses and mark matching invitations responded.

    Records the response id + timestamp, and (best-effort) extracts the
    satisfaction score and free-text comment from the response body using the
    survey's question structure. A failure fetching that structure leaves
    score/comment null but still marks the invitation responded.
    """
    settings = get_settings()
    survey_id = settings.surveymonkey_csat_survey_id
    collector_id = settings.surveymonkey_csat_collector_id
    if not survey_id or not collector_id:
        raise ValueError("CSAT survey_id and collector_id are not configured")

    invitations = session.exec(
        select(CsatInvitation)
        .where(CsatInvitation.status == "sent")
        .where(CsatInvitation.surveymonkey_recipient_id.is_not(None))
    ).all()
    if not invitations:
        return {"updated": 0}

    client = _client()
    responses = client.list_all_survey_responses_bulk(survey_id)
    updates = build_csat_response_updates(invitations, responses, collector_id)

    # Only pay for the survey-structure fetch if we actually have responses to
    # score; a details failure must not block marking invitations responded.
    structure = None
    responses_by_id = {str(response.get("id")): response for response in responses}
    if updates:
        try:
            structure = parse_csat_survey_structure(client.get_survey_details(survey_id))
        except Exception:
            structure = None

    invitations_by_id = {inv.id: inv for inv in invitations}
    for update in updates:
        invitation = invitations_by_id[update["invitation_id"]]
        invitation.surveymonkey_response_id = update["response_id"]
        invitation.responded_at = _parse_sm_datetime(update["responded_at"])
        invitation.status = "responded"
        if structure is not None:
            full_response = responses_by_id.get(update["response_id"])
            if full_response is not None:
                score, comment = extract_csat_score_and_comment(full_response, structure)
                invitation.score = score
                invitation.comment = comment
        invitation.updated_at = datetime.utcnow()
        session.add(invitation)

    session.commit()
    return {"updated": len(updates)}


# --- One-time standing setup ---------------------------------------------

def create_standing_csat_survey_from_template() -> dict:
    """Clone the CSAT template into a standing survey + email collector.

    Run once; put the returned ids in SURVEYMONKEY_CSAT_SURVEY_ID and
    SURVEYMONKEY_CSAT_COLLECTOR_ID. Real SurveyMonkey side effects — this
    creates a live survey and collector.
    """
    settings = get_settings()
    template_id = settings.surveymonkey_csat_template_survey_id
    if not template_id:
        raise RuntimeError("SURVEYMONKEY_CSAT_TEMPLATE_SURVEY_ID is not configured")

    client = _client()
    survey_id = clone_surveymonkey_survey_from_template(
        client=client,
        template_survey_id=template_id,
        title="Candidate Support CSAT",
    )
    collector = client.create_email_collector(
        survey_id=survey_id, name="Candidate Support CSAT Collector"
    )
    collector_id = extract_surveymonkey_id(collector, "collector")
    return {"survey_id": survey_id, "collector_id": collector_id}
