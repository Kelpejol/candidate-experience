import re
from typing import Literal

from pydantic import BaseModel

from app.core.helpdesk_taxonomy import SENSITIVE_CATEGORIES
from app.services.helpdesk_classifier import TicketClassification

Action = Literal["draft_reply", "auto_reply", "route_to_human", "tag_only", "ask_clarification", "request_attachment"]

# Deterministic sensitivity backstop: word STEMS matched at word boundaries,
# so "complaint", "complaining", "complained" all hit "complain". Matched on
# the subject only. This is the last line of defense beneath the LLM's
# sensitivity judgment — deliberately dumb, deterministic, and auditable.
COMPLAINT_STEMS = (
    "complain", "disput", "appeal", "legal", "lawyer", "attorney", "sue",
    "lawsuit", "court", "fraud", "scam", "cheat", "unfair", "wrongful",
    "discriminat", "harass", "threat", "injustice", "compensat", "refund",
    "escalat", "unacceptable", "mislead", "deceiv", "exploit", "petition",
)

_COMPLAINT_PATTERN = re.compile(
    r"\b(" + "|".join(COMPLAINT_STEMS) + r")\w*", re.IGNORECASE
)

# WhatsApp-only backstop: a candidate explicitly asking for a person must
# always escalate, even if the question would otherwise be answerable — a
# conversational channel is exactly where someone says this and expects it
# to be honored immediately. Plain substrings rather than word-boundary
# stems (like COMPLAINT_STEMS) because these are multi-word phrases, not
# single roots.
HUMAN_REQUEST_PHRASES = (
    "speak to a human", "speak to someone", "speak with a human",
    "speak with someone", "speak to a person", "speak with a person",
    "talk to a human", "talk to someone", "talk to an agent",
    "talk to a person", "talk to a real person",
    "real person", "human agent", "human being",
    "customer service rep", "customer service representative",
    "connect me to a person", "connect me with a human",
    "connect me to a human", "get me a human",
)

_ACKNOWLEDGEMENT_MARKERS = (
    "ok", "okay", "noted", "received", "well received", "duly noted",
    "thank you", "thanks", "many thanks", "alright", "all right",
    "confirmed", "i confirm", "i acknowledge", "acknowledged",
)

_QUESTION_MARKERS = (
    "?", "how", "what", "when", "where", "why", "can", "could", "please",
    "help", "issue", "problem", "unable", "can't", "cannot", "not able",
    "error", "failed", "failure", "reschedule", "change", "wrong",
)


def detect_human_request(text: str) -> str | None:
    """Return the matched phrase if the candidate explicitly asked for a
    person, else None. Case-insensitive plain substring match."""
    lowered = text.lower()
    for phrase in HUMAN_REQUEST_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def detect_simple_acknowledgement(text: str) -> str | None:
    """Return a matched acknowledgement if the latest candidate message is
    just a receipt/thanks/note, not a question or support request."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = normalized.strip(" .,!;:-")
    if not normalized:
        return None
    if len(normalized) > 120:
        return None
    words = normalized.split()
    if any(marker in normalized for marker in _QUESTION_MARKERS):
        return None
    for marker in _ACKNOWLEDGEMENT_MARKERS:
        if normalized == marker or normalized.startswith(f"{marker} "):
            return marker
    if len(words) <= 4 and any(marker in normalized for marker in _ACKNOWLEDGEMENT_MARKERS):
        return normalized
    return None


class TicketDecision(BaseModel):
    action: Action
    rule: str      # which rule fired — for the audit log
    reason: str


def decide_ticket_action(
    classification: TicketClassification,
    subject: str = "",
    zoho_sentiment: str | None = None,
    body: str = "",
) -> TicketDecision:
    """First matching rule wins — ordered most-restrictive first.

    The first two rules are deterministic backstops that do not depend on
    the LLM at all: complaint/dispute wording in the subject OR body, and
    Zoho's own sentiment signal. The LLM's judgment applies only below them.
    """

    # Scan the body too, not just the subject: candidates routinely file a
    # neutral subject ("Test page not loading") and escalate in the message
    # ("I will sue you"). Subject-only scanning let those reach an auto-draft.
    match = _COMPLAINT_PATTERN.search(f"{subject} {body}")
    if match:
        return TicketDecision(
            action="route_to_human",
            rule="complaint_keyword_backstop",
            reason=f'Ticket contains dispute/complaint wording ("{match.group(0)}").',
        )

    if (zoho_sentiment or "").upper() == "NEGATIVE":
        return TicketDecision(
            action="route_to_human",
            rule="negative_sentiment_backstop",
            reason="Zoho flagged this ticket's sentiment as negative.",
        )

    if classification.sensitivity_detected or classification.issue_category in SENSITIVE_CATEGORIES:
        return TicketDecision(
            action="route_to_human",
            rule="sensitive_never_automated",
            reason=f"Sensitive ({classification.issue_category}); an officer must handle it.",
        )

    if classification.confidence_label == "low":
        return TicketDecision(
            action="route_to_human",
            rule="low_confidence",
            reason="Classifier is unsure; a human should read this one.",
        )

    if classification.issue_category == "spam_or_irrelevant":
        # A reply to our own thread is never spam, whatever the model says —
        # tag_only on a real candidate would silently ignore them.
        if subject.strip().lower().startswith("re:"):
            return TicketDecision(
                action="route_to_human",
                rule="spam_claim_on_reply_backstop",
                reason="Model called this spam, but it replies to our own email thread; a human should look.",
            )
        return TicketDecision(
            action="tag_only",
            rule="spam_no_reply",
            reason="Spam/irrelevant; tag it and move on.",
        )

    acknowledgement = detect_simple_acknowledgement(body)
    if acknowledgement:
        return TicketDecision(
            action="tag_only",
            rule="simple_acknowledgement_no_reply_needed",
            reason=f'Candidate only acknowledged the message ("{acknowledgement}"); no response required.',
        )

    if classification.issue_category == "availability_confirmation" and classification.confidence_label == "high":
        return TicketDecision(
            action="tag_only",
            rule="confirmation_no_reply_needed",
            reason="Candidate confirmed availability; no response required.",
        )

    if classification.issue_category == "business_or_partnership":
        return TicketDecision(
            action="route_to_human",
            rule="non_candidate_contact",
            reason="Business/partnership contact; belongs with a person, not candidate support.",
        )

    return TicketDecision(
        action="draft_reply",
        rule="answerable_draft_first",
        reason=f"In-scope {classification.issue_category}; AI drafts, officer approves.",
    )
