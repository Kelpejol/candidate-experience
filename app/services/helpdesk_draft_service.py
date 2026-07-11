"""Grounded draft generation for helpdesk email replies.

Writes a reply using ONLY retrieved KB excerpts. The model has one escape
hatch: if the excerpts don't actually answer the candidate's question it
must output ESCALATE, which the pipeline turns into route_to_human. The
draft is never sent by us — an officer approves and sends it from Zoho.
"""

import httpx

from app.core.config import get_settings

ESCALATE_MARKER = "ESCALATE"

DRAFT_SYSTEM_PROMPT = (
    "You draft email replies for the Candidate Experience team at Dragnet "
    "Solutions, a recruitment assessment company in Nigeria. A human officer "
    "reviews every draft before sending.\n\n"
    "Rules:\n"
    "- Answer using ONLY the knowledge-base excerpts provided. Do not invent "
    "links, credentials, dates, policies, or promises.\n"
    "- If the excerpts do not actually answer the candidate's question, "
    f"output exactly {ESCALATE_MARKER} and nothing else.\n"
    "- Warm, professional, concise. Address the candidate by first name if "
    "known, otherwise 'Dear Candidate'.\n"
    "- Plain text only, no markdown or HTML.\n"
    "- Sign off exactly as:\n  Candidate Experience Team\n  Dragnet Solutions"
)


def generate_draft_reply(
    subject: str,
    candidate_message: str,
    kb_chunks: list[dict],
    candidate_name: str | None = None,
) -> str | None:
    """Return draft text, or None when the model escalates."""
    settings = get_settings()

    excerpts = "\n\n---\n\n".join(chunk["text"] for chunk in kb_chunks)
    user_content = (
        f"Knowledge-base excerpts:\n\n{excerpts}\n\n====\n\n"
        f"Candidate name: {candidate_name or 'unknown'}\n"
        f"Email subject: {subject}\n\n"
        f"Candidate's message:\n{candidate_message}\n\n"
        "Write the reply now (or ESCALATE)."
    )

    resp = httpx.post(
        f"{settings.inference_base_url}/chat",
        headers={"Authorization": f"Bearer {settings.inference_api_key}"},
        json={
            "messages": [
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 600,
        },
        timeout=120,
    )
    resp.raise_for_status()
    output = resp.json()["output"].strip()

    if ESCALATE_MARKER in output[:40]:
        return None
    return output
