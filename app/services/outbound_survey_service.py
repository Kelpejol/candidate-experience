"""Turn a campaign's SurveyMonkey survey into questions the outbound voice
agent can ask by phone.

The outbound survey agent follows a fixed, human-authored script (greeting,
asking permission, closing) but the *questions themselves* are pulled live
from whichever survey the campaign uses — so the call always matches the email
survey the candidate didn't answer.

`build_spoken_questions` is the pure mapper (SurveyMonkey's page/question JSON
-> a flat, spoken-friendly list) and is unit-tested with no network.
`get_campaign_survey_questions` wires it to a campaign + the SurveyMonkey API.
"""

import html
import re
from datetime import datetime

from sqlmodel import Session, select

from app.core.config import get_settings
from app.integrations.surveymonkey_client import SurveyMonkeyClient
from app.models.campaign_candidate import CampaignCandidate
from app.models.outbound_survey_answer import OutboundSurveyAnswer
from app.services.campaign_service import (
    get_campaign_by_id,
    get_campaign_candidate_by_id,
    get_outbound_call_attempt_by_id,
    suppress_person_from_calls,
)

# SurveyMonkey headings can carry HTML (bold, line breaks, entities). Voice
# needs plain text, so strip tags and collapse whitespace before speaking.
_TAG_RE = re.compile(r"<[^>]+>")

# Bounds on what the agent may write per answer. A spoken question/answer is
# short; these only clip pathological input (a stuck model, a garbled
# transcript) so one call can't write unbounded rows.
MAX_QUESTION_LENGTH = 1_000
MAX_ANSWER_LENGTH = 4_000


def _clean_text(raw: str | None) -> str:
    """Strip HTML tags/entities and collapse whitespace for speech."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = " ".join(text.split())
    # Tags become spaces, which can leave a gap before punctuation
    # ("resolved <b>?" -> "resolved ?"); tidy it so it reads naturally aloud.
    text = re.sub(r"\s+([?.!,;:])", r"\1", text)
    return text.strip()


def _question_heading(question: dict) -> str:
    """The question's prompt text, cleaned — first non-empty heading."""
    for heading in question.get("headings") or []:
        cleaned = _clean_text(heading.get("heading"))
        if cleaned:
            return cleaned
    return ""


def _choice_texts(question: dict) -> list[str]:
    """The selectable answer options, cleaned. Drops any 'N/A' choice, which
    reads awkwardly aloud and isn't a real answer."""
    answers = question.get("answers") or {}
    options: list[str] = []
    for choice in answers.get("choices") or []:
        if choice.get("is_na"):
            continue
        text = _clean_text(choice.get("text"))
        if text:
            options.append(text)
    return options


def build_spoken_questions(details: dict) -> list[dict]:
    """Map a SurveyMonkey survey-details payload to spoken-friendly questions.

    Returns an ordered list of {position, text, type, options}:
      - type "choice" — pick one (single_choice / rating-style matrix); options listed
      - type "multi"  — pick any that apply (multiple_choice); options listed
      - type "text"   — free-spoken answer (open_ended and anything else); options empty

    Presentation blocks (descriptive text, no answer) and questions with no
    heading are skipped. Positions are 1-based over the questions we keep.
    """
    questions: list[dict] = []
    position = 0

    for page in details.get("pages") or []:
        for question in page.get("questions") or []:
            family = (question.get("family") or "").lower()

            # Descriptive text blocks aren't questions — nothing to ask.
            if family == "presentation":
                continue

            heading = _question_heading(question)
            if not heading:
                continue

            if family in ("single_choice", "matrix"):
                qtype, options = "choice", _choice_texts(question)
            elif family == "multiple_choice":
                qtype, options = "multi", _choice_texts(question)
            else:
                # open_ended, datetime, demographic, numeric, … — just ask it.
                qtype, options = "text", []

            position += 1
            questions.append(
                {
                    "position": position,
                    "text": heading,
                    "type": qtype,
                    "options": options,
                }
            )

    return questions


def _client() -> SurveyMonkeyClient:
    settings = get_settings()
    if not settings.surveymonkey_access_token:
        raise RuntimeError("SurveyMonkey access token is not configured")
    return SurveyMonkeyClient(
        base_url=settings.surveymonkey_base_url,
        access_token=settings.surveymonkey_access_token,
    )


def get_campaign_survey_questions(
    campaign_id: str,
    session: Session,
    client: SurveyMonkeyClient | None = None,
) -> list[dict]:
    """Fetch and map the spoken questions for a campaign's survey.

    Raises LookupError if the campaign doesn't exist and ValueError if it has
    no survey configured — both map to clean 4xx responses at the route. The
    SurveyMonkey client is injectable so the mapping can be tested without a
    network call.
    """
    campaign = get_campaign_by_id(campaign_id, session)
    if not campaign:
        raise LookupError(f"Campaign {campaign_id} not found")
    if not campaign.survey_id:
        raise ValueError("Campaign has no survey configured")

    client = client or _client()
    details = client.get_survey_details(campaign.survey_id)
    return build_spoken_questions(details)


def record_survey_answer(
    session: Session,
    campaign_id: str,
    outbound_attempt_id: str,
    question: str,
    answer: str,
    position: int | None = None,
    answer_type: str | None = None,
) -> OutboundSurveyAnswer:
    """Record (or update) one answer captured during an outbound survey call.

    The candidate is derived from the attempt, never trusted from the agent.
    Idempotent: a re-asked or corrected answer replaces the earlier one for the
    same question rather than duplicating — keyed on `position` when present
    (stable across re-wordings), otherwise the question text.

    Over-long values are truncated rather than rejected: this runs mid-call, and
    failing the tool over a rambling answer would break the conversation. Spoken
    answers are short, so the caps only bite on malformed input.

    Raises LookupError if the attempt doesn't exist (unknown/stale id) and
    ValueError if the question or answer is blank.
    """
    question = (question or "").strip()[:MAX_QUESTION_LENGTH]
    answer = (answer or "").strip()[:MAX_ANSWER_LENGTH]
    if not question:
        raise ValueError("question is required")
    if not answer:
        raise ValueError("answer is required")

    attempt = get_outbound_call_attempt_by_id(
        campaign_id=campaign_id,
        attempt_id=outbound_attempt_id,
        session=session,
    )
    if not attempt:
        raise LookupError("Outbound call attempt not found")

    # Match on the question text as well as the position. Agents routinely
    # reuse or default a position index, and keying on position alone would let
    # a different question silently overwrite an earlier answer — losing it with
    # no error while still reporting success.
    statement = (
        select(OutboundSurveyAnswer)
        .where(OutboundSurveyAnswer.outbound_attempt_id == outbound_attempt_id)
        .where(OutboundSurveyAnswer.question == question)
    )
    if position is not None:
        statement = statement.where(OutboundSurveyAnswer.position == position)
    existing = session.exec(statement).first()

    if existing:
        existing.question = question
        existing.answer = answer
        existing.answer_type = answer_type or existing.answer_type
        existing.updated_at = datetime.utcnow()
        row = existing
    else:
        row = OutboundSurveyAnswer(
            outbound_attempt_id=outbound_attempt_id,
            campaign_id=attempt.campaign_id,
            candidate_id=attempt.candidate_id,
            position=position,
            question=question,
            answer_type=answer_type,
            answer=answer,
        )

    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_campaign_survey_answers(session: Session, campaign_id: str) -> list[dict]:
    """All survey answers collected by voice for a campaign, grouped by the
    call they came from and ordered within a call by question position.

    Ordering by the row timestamp alone would return answers in reverse
    question order and interleave concurrent calls, so the attempt is the
    primary key and position the secondary.

    Each row is enriched with the candidate's name (resolved in one query, no
    N+1) so the UI can group answers per candidate without extra round-trips.
    """
    answers = session.exec(
        select(OutboundSurveyAnswer)
        .where(OutboundSurveyAnswer.campaign_id == campaign_id)
        .order_by(
            OutboundSurveyAnswer.outbound_attempt_id,
            OutboundSurveyAnswer.position.asc(),
            OutboundSurveyAnswer.created_at.asc(),
        )
    ).all()

    names = {
        candidate.id: candidate.candidate_name
        for candidate in session.exec(
            select(CampaignCandidate).where(
                CampaignCandidate.campaign_id == campaign_id
            )
        ).all()
    }

    return [
        {
            "id": answer.id,
            "outbound_attempt_id": answer.outbound_attempt_id,
            "candidate_id": answer.candidate_id,
            "candidate_name": names.get(answer.candidate_id),
            "position": answer.position,
            "question": answer.question,
            "answer_type": answer.answer_type,
            "answer": answer.answer,
            "created_at": answer.created_at,
        }
        for answer in answers
    ]


def attempt_has_survey_answers(session: Session, outbound_attempt_id: str) -> bool:
    """Whether any survey answers were captured for an attempt — a reliable,
    self-owned signal that the candidate responded by call (used to back the
    post-call webhook's completion detection)."""
    statement = (
        select(OutboundSurveyAnswer.id)
        .where(OutboundSurveyAnswer.outbound_attempt_id == outbound_attempt_id)
        .limit(1)
    )
    return session.exec(statement).first() is not None


def record_opt_out(
    session: Session,
    campaign_id: str,
    outbound_attempt_id: str,
) -> None:
    """Honor a caller's request not to be contacted again.

    Terminal and durable, and applied to the PERSON rather than just this
    campaign's row: a candidate exists once per campaign, so suppressing only
    the current row would let the next campaign call them again. The post-call
    webhook is written to preserve this rather than downgrade it.

    Raises LookupError if the attempt or its candidate doesn't exist — the
    agent must never be told an opt-out was honored when nothing was written.
    """
    attempt = get_outbound_call_attempt_by_id(
        campaign_id=campaign_id,
        attempt_id=outbound_attempt_id,
        session=session,
    )
    if not attempt:
        raise LookupError("Outbound call attempt not found")

    candidate = get_campaign_candidate_by_id(
        campaign_id=attempt.campaign_id,
        candidate_id=attempt.candidate_id,
        session=session,
    )
    if not candidate:
        raise LookupError("Candidate for this attempt not found")

    # Every campaign this person appears in, not just this one.
    suppress_person_from_calls(
        session, email=candidate.email, phone=candidate.phone
    )
    candidate.opted_out_call = True
    candidate.call_status = "opted_out"
    session.add(candidate)

    attempt.status = "opted_out"
    attempt.disposition = "caller_opted_out"
    if attempt.ended_at is None:
        attempt.ended_at = datetime.utcnow()
    session.add(attempt)
    session.commit()
