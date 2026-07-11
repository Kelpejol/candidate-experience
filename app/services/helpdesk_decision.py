import re
from typing import Literal

from pydantic import BaseModel

from app.core.helpdesk_taxonomy import SENSITIVE_CATEGORIES
from app.services.helpdesk_classifier import TicketClassification

Action = Literal["draft_reply", "route_to_human", "tag_only"]

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


class TicketDecision(BaseModel):
    action: Action
    rule: str      # which rule fired — for the audit log
    reason: str


def decide_ticket_action(
    classification: TicketClassification,
    subject: str = "",
    zoho_sentiment: str | None = None,
) -> TicketDecision:
    """First matching rule wins — ordered most-restrictive first.

    The first two rules are deterministic backstops that do not depend on
    the LLM at all: complaint/dispute wording in the subject, and Zoho's
    own sentiment signal. The LLM's judgment applies only below them.
    """

    match = _COMPLAINT_PATTERN.search(subject)
    if match:
        return TicketDecision(
            action="route_to_human",
            rule="complaint_keyword_backstop",
            reason=f'Subject contains dispute/complaint wording ("{match.group(0)}").',
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
