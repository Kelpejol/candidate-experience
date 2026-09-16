"""Retrieval + grounding decision for the Calling Agent KB.

Turns a caller's question into the backend SIGNAL our escalation design needs:
either grounded content to answer from, or "no confident answer" (which the
agent treats as the cue to escalate). Read side of the seam — only calls
query()/embed_text(), never Chroma directly.
"""

from app.core.config import get_settings
from app.services.voice_kb_ingest import embed_text   # SAME embedder as indexing — the rule
from app.services.voice_kb_store import query

# Calibrated cutoff (July 2026): real hits scored <= 0.32, out-of-scope >= 0.46.
# This mirrors the helpdesk KB gate and is overridable via KB_GROUNDING_THRESHOLD.
# It was previously an uncalibrated 0.55 — above the out-of-scope floor — which
# let the agent read off-topic content aloud to callers instead of escalating.
GROUNDING_THRESHOLD = 0.40

# The live in-call embed call fails fast into escalation rather than leaving
# the caller in silence while a slow gateway is retried.
LIVE_EMBED_TIMEOUT = 8.0


def retrieve_for_answer(
    question: str,
    k: int = 3,
    threshold: float | None = None,
    campaign: str | None = None,
    extra_scopes: list[str] | None = None,
) -> dict:
    """Backend grounding decision for a question, scoped to a campaign.

    `campaign` is the KB scope tag (a valid, active campaign's scope) or None
    for general-only. `extra_scopes` adds further scope tags to match
    alongside it — e.g. the campaign's assessment-tool scope (see
    campaign_scope_service.campaign_tool_scope), so a question can be
    answered from general OR tool-specific OR campaign-specific content in
    one query. `threshold` defaults to the configured KB_GROUNDING_THRESHOLD
    so the gate can be tuned without a code change.
    Returns {answer_available, content, sources, best_distance};
    answer_available is False when nothing is close enough -> agent escalates.
    """
    if threshold is None:
        threshold = get_settings().kb_grounding_threshold
    hits = query(
        embed_text(question, timeout=LIVE_EMBED_TIMEOUT),
        k=k,
        campaign=campaign,
        extra_scopes=extra_scopes,
    )
    relevant = [h for h in hits if h["distance"] <= threshold]
    return {
        "answer_available": bool(relevant),
        "content": [h["document"] for h in relevant] or None,
        "sources": [h["metadata"].get("source") for h in relevant],
        "best_distance": hits[0]["distance"] if hits else None,
    }
