from sqlmodel import Session
import re


from app.core.config import get_settings
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_classifier import classify_ticket
from app.services.helpdesk_decision import TicketDecision, decide_ticket_action
from app.services.helpdesk_draft_service import generate_draft_reply
from app.services.helpdesk_kb_service import GroundingResult, retrieve_grounding


def apply_grounding_gate(
    decision: TicketDecision, grounding: GroundingResult
) -> tuple[TicketDecision, str]:
    """Gate a draft_reply decision on KB coverage.

    Returns (possibly overridden decision, grounding_status). Pure logic —
    the retrieval itself happens in the caller — so the guarantee "no KB
    coverage means no draft" is unit-testable without a network.
    """
    if grounding.grounded:
        return decision, "grounded"

    best = f"{grounding.best_distance:.2f}" if grounding.best_distance is not None else "n/a"
    return (
        TicketDecision(
            action="route_to_human",
            rule="kb_grounding_missing",
            reason=f"KB has nothing close enough to this question (best distance {best}); not drafting.",
        ),
        "missing",
    )


def process_ticket(session: Session, zoho_client, mirror: HelpdeskTicketMirror) -> HelpdeskAIAction:
    """Classify a mirrored ticket, decide, generate a draft, and record it all.

    Caller commits. Zoho is only written when settings.helpdesk_draft_execute
    is True (and then only an unsent draft an officer must approve); with the
    flag off this is a full dry run — drafts land in HelpdeskAIAction.draft_text.
    """
    body = get_latest_candidate_message(zoho_client, mirror.zoho_ticket_id)

    classification = classify_ticket(subject=mirror.subject or "", body=body)
    decision = decide_ticket_action(
        classification,
        subject=mirror.subject or "",
        zoho_sentiment=mirror.sentiment,
    )

    # Grounding gate: a draft may only happen when the KB actually covers
    # the question. Retrieval failure (e.g. KB never synced) counts as
    # ungrounded — degrade to a human, never crash ticket processing.
    grounding_status = None
    grounding = None
    if decision.action == "draft_reply":
        try:
            grounding = retrieve_grounding(body or mirror.subject or "")
        except Exception:
            grounding = GroundingResult(grounded=False, best_distance=None, chunks=[])
        decision, grounding_status = apply_grounding_gate(decision, grounding)

    # Draft generation: grounded draft_reply decisions get a reply written
    # from the retrieved chunks. The model may still escalate mid-draft if
    # the chunks don't truly answer — that flips the decision to a human.
    draft_text = None
    executed = False
    if decision.action == "draft_reply" and grounding is not None:
        first_name = (mirror.candidate_name or "").split(" ")[0] or None
        draft_text = generate_draft_reply(
            subject=mirror.subject or "",
            candidate_message=body,
            kb_chunks=grounding.chunks,
            candidate_name=first_name,
        )
        if draft_text is None:
            decision = TicketDecision(
                action="route_to_human",
                rule="draft_writer_escalated",
                reason="Draft-writer judged the KB excerpts insufficient to answer; escalating.",
            )
        elif get_settings().helpdesk_draft_execute:
            zoho_client.create_draft_reply(
                ticket_id=mirror.zoho_ticket_id,
                content=draft_text,
                from_email_address=get_settings().helpdesk_from_email,
                to=mirror.candidate_email or "",
                content_type="plainText",
            )
            executed = True

    # Tagging value lands on the mirror even in dry-run — classification
    # is safe to persist; only *actions* are held back.
    mirror.issue_category = classification.issue_category
    mirror.tool_name = classification.tool_name or mirror.tool_name
    mirror.campaign_name = classification.campaign_name or mirror.campaign_name
    session.add(mirror)

    action = HelpdeskAIAction(
        zoho_ticket_id=mirror.zoho_ticket_id,
        action_type=decision.action,
        rule=decision.rule,
        issue_category=classification.issue_category,
        tool_name=classification.tool_name,
        campaign_name=classification.campaign_name,
        confidence_label=classification.confidence_label,
        sensitivity_detected=classification.sensitivity_detected,
        grounding_status=grounding_status,
        draft_text=draft_text,
        reason=decision.reason,
        executed=executed,
    )
    session.add(action)
    return action



def get_latest_candidate_message(zoho_client, zoho_ticket_id: str) -> str:
    """Return the newest INCOMING message's text — what the candidate last said.

    The naive latest thread is often our own outbound reply; classifying
    that mislabels tickets (see #91942). Falls back to the latest thread
    of any direction if no incoming thread exists.
    """
    threads = zoho_client.list_ticket_threads(zoho_ticket_id).get("data", [])
    incoming = [t for t in threads if t.get("direction") == "in"]
    candidates = sorted(incoming or threads, key=lambda t: t.get("createdTime") or "")

    if not candidates:
        return ""

    newest = candidates[-1]
    detail = zoho_client.get_thread(zoho_ticket_id, str(newest["id"]))
    text = detail.get("content") or detail.get("summary") or newest.get("summary") or ""
    return re.sub(r"<[^>]+>", " ", text).strip()