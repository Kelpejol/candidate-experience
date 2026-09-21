import httpx
from pydantic import ValidationError
from pydantic import BaseModel
from typing import Literal

from app.core.config import get_settings
from pydantic import field_validator

from app.core.helpdesk_taxonomy import (
    HELPDESK_TAXONOMY,
    ISSUE_CATEGORIES,
    SENSITIVE_CATEGORIES,
)
from app.core.vocabulary import ALLOWED_TOOLS

# Where an off-taxonomy category lands. Deliberately a SENSITIVE category so an
# unrecognised label routes to a human instead of falling through to the
# permissive default action.
UNKNOWN_CATEGORY_FALLBACK = "complaint"


class TicketClassification(BaseModel):
    issue_category: str
    tool_name: str | None = None   # "FOT", "Scholastica", or None if unclear
    campaign_name: str | None = None  # e.g. "Lafarge Africa Graduate Trainee"
    sensitivity_detected: bool
    confidence_label: Literal["high", "medium", "low"]
    reason: str                    # one sentence: why this classification

    @field_validator("issue_category", mode="before")
    @classmethod
    def coerce_to_taxonomy(cls, v):
        """Map anything off-taxonomy onto a safe, sensitive category.

        Routing rules match category names EXACTLY, so a hallucinated or
        miscased label ("Complaint", "results_question", "") would silently
        skip the sensitive-category gate and become an auto-draft — turning the
        strictest rule into the most permissive one. Case/spacing slips are
        normalised; anything still unrecognised is treated as sensitive so a
        human sees it.
        """
        if not isinstance(v, str):
            return UNKNOWN_CATEGORY_FALLBACK
        normalized = v.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in ISSUE_CATEGORIES:
            return normalized
        return UNKNOWN_CATEGORY_FALLBACK


def _category_block() -> str:
    """Render the taxonomy as prompt text: one definition line per category,
    with real ticket subjects as few-shot examples underneath."""
    lines = []
    for name, cfg in HELPDESK_TAXONOMY.items():
        lines.append(f"- {name}: {cfg['description']}")
        for example in cfg["examples"]:
            lines.append(f'    e.g. "{example}"')
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You classify candidate support tickets for Dragnet Solutions, a recruitment "
    f"assessment company in Nigeria. Candidates take tests on tools: {sorted(ALLOWED_TOOLS)}.\n\n"
    f"Choose issue_category from exactly these:\n{_category_block()}\n\n"
    f"Set sensitivity_detected true when the category is one of {sorted(SENSITIVE_CATEGORIES)}, "
    "or the candidate is angry, threatening, or disputing a result. Also set it true when "
    "the candidate frames their message as a complaint or expresses dissatisfaction, even "
    "if the underlying issue is technical.\n"
    "The campaign/test name usually appears in the subject line — extract it even when "
    "the body doesn't repeat it.\n"
    "The message may include a labelled recent conversation and OCR excerpts. Classify "
    "the latest candidate message/request first; use prior officer/candidate messages "
    "and OCR only as supporting context. If the latest candidate message corrects "
    "earlier context, the latest candidate message wins.\n"
    "Use confidence_label \"low\" whenever the ticket is ambiguous.\n\n"
    "Respond with ONLY a JSON object — no markdown fences, no explanation — with exactly "
    "these keys: issue_category (string), tool_name (string or null), campaign_name "
    "(string or null), sensitivity_detected (boolean), confidence_label "
    "(\"high\"|\"medium\"|\"low\"), reason (string, one sentence)."
)

REPAIR_SYSTEM_PROMPT = (
    "You repair malformed helpdesk ticket classifications. Convert the provided "
    "classifier output into exactly one valid JSON object matching this schema: "
    "issue_category (string), tool_name (string or null), campaign_name "
    "(string or null), sensitivity_detected (boolean), confidence_label "
    "(\"high\"|\"medium\"|\"low\"), reason (string). Use the original ticket "
    "only to resolve missing or invalid fields. Respond with JSON only."
)


def _post_classification(messages: list[dict[str, str]]) -> str:
    settings = get_settings()

    resp = httpx.post(
        f"{settings.inference_base_url}/chat",
        headers={"Authorization": f"Bearer {settings.inference_api_key}"},
        json={
            "messages": messages,
            "max_tokens": 1000,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["output"]


def classify_ticket(subject: str, body: str) -> TicketClassification:
    user_message = f"Subject: {subject}\n\nMessage:\n{body}"
    output = _post_classification([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ])
    try:
        return parse_classification(output)
    except ValidationError:
        repaired = _post_classification([
            {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Subject: {subject}\n\nMessage:\n{body}\n\n"
                    f"Malformed classifier output:\n{output}"
                ),
            },
        ])
        return parse_classification(repaired)


def parse_classification(output: str) -> TicketClassification:
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return TicketClassification.model_validate_json(text.strip())
