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
    "reviews every draft before sending. Match the house style officers "
    "already use in real replies.\n\n"
    "Rules:\n"
    "- Answer using ONLY the knowledge-base excerpts provided. Do not invent "
    "links, credentials, dates, policies, or promises.\n"
    "- If the excerpts do not actually answer the candidate's question, "
    f"output exactly {ESCALATE_MARKER} and nothing else.\n"
    "- Open with 'Dear [Name],' if the candidate's name is known, otherwise "
    "'Dear Candidate,'. On the next line, write exactly: 'We warmly "
    "acknowledge receipt of your email.' Then give the answer.\n"
    "- Warm, professional, concise.\n"
    "- Plain text only, no markdown or HTML.\n"
    "- Do not add a sign-off, closing line, or signature of any kind — end "
    "right after the answer."
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
            # Low, near-deterministic: this call's own judgment of "do these
            # excerpts truly answer this?" was observed flip-flopping between
            # ESCALATE and a real answer across repeated calls with IDENTICAL
            # input (found during the 2026-09-15 historical evaluation — same
            # KB chunks, same question, answered 2 of 3 tries and escalated
            # 1 of 3). Retrieval was already confirmed correct in every case;
            # this was sampling variance in generation, not a retrieval or
            # classification bug. Default temperature amplified it.
            "temperature": 0.1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    output = resp.json()["output"].strip()

    # Scan the WHOLE output, not just the opening: a model that hedges before
    # bailing ("I cannot answer this from the excerpts. ESCALATE") would
    # otherwise have that sentence placed on a real customer ticket. The marker
    # never legitimately appears in a candidate-facing reply.
    # An empty/whitespace response is also an escalation, not a blank draft.
    if not output or ESCALATE_MARKER in output:
        return None
    return output


# WhatsApp is conversational and real-time — unlike email, there's no human
# reviewing before this goes out, so the same anti-hallucination/ESCALATE
# rules apply, but the tone and shape are deliberately different: short,
# no subject line, no formal salutation or sign-off (nobody writes "Dear
# Candidate" in a WhatsApp chat).
WHATSAPP_SYSTEM_PROMPT = (
    "You reply to candidates over WhatsApp for the Candidate Experience team "
    "at Dragnet Solutions, a recruitment assessment company in Nigeria. This "
    "reply is sent immediately with no human review — get it right.\n\n"
    "Rules:\n"
    "- Answer using ONLY the knowledge-base excerpts provided. Do not invent "
    "links, credentials, dates, policies, or promises.\n"
    "- If the excerpts do not actually answer the candidate's question, "
    f"output exactly {ESCALATE_MARKER} and nothing else.\n"
    "- Short and conversational, like a real WhatsApp message — a sentence "
    "or two, not an email. No subject line, no formal salutation ('Dear "
    "Candidate'), no sign-off.\n"
    "- Plain text only, no markdown or HTML."
)


def generate_whatsapp_reply(
    candidate_message: str,
    kb_chunks: list[dict],
    candidate_name: str | None = None,
) -> str | None:
    """Return a short WhatsApp-toned reply, or None when the model escalates."""
    settings = get_settings()

    excerpts = "\n\n---\n\n".join(chunk["text"] for chunk in kb_chunks)
    user_content = (
        f"Knowledge-base excerpts:\n\n{excerpts}\n\n====\n\n"
        f"Candidate name: {candidate_name or 'unknown'}\n\n"
        f"Candidate's WhatsApp message:\n{candidate_message}\n\n"
        "Write the reply now (or ESCALATE)."
    )

    resp = httpx.post(
        f"{settings.inference_base_url}/chat",
        headers={"Authorization": f"Bearer {settings.inference_api_key}"},
        json={
            "messages": [
                {"role": "system", "content": WHATSAPP_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 300,
            # See generate_draft_reply's comment: low temperature to reduce
            # sampling variance in the ESCALATE-or-answer judgment, since this
            # path sends immediately with no human review — consistency here
            # matters even more than for the reviewed-draft path.
            "temperature": 0.1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    output = resp.json()["output"].strip()

    if not output or ESCALATE_MARKER in output:
        return None
    return output
