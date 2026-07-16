from sqlmodel import Session, select
import re


from app.core.config import get_settings
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_classifier import classify_ticket
from app.services.helpdesk_decision import TicketDecision, decide_ticket_action
from app.services.helpdesk_draft_service import generate_draft_reply
from app.services.helpdesk_executor_service import execute_ticket_action
from app.services.helpdesk_kb_service import GroundingResult, retrieve_grounding

# Decision action -> mirror.ai_disposition value (plan doc vocabulary).
_DISPOSITION_BY_ACTION = {
    "draft_reply": "drafted",
    "route_to_human": "routed_to_human",
    "tag_only": "no_action",
}

# Mail-server bounce notifications (mailer-daemon / MS Exchange NDRs) land in
# Zoho as regular tickets from a bulk campaign send with bad addresses. They
# are not candidates and burn a classifier call for nothing — filtered out
# before any LLM call, not just routed after one. Detected primarily by the
# "from" address being on our OWN domain (a real candidate never emails from
# dragnet-solutions.com), with the subject prefix as a backup signal.
_OUR_DOMAIN_SUFFIX = "@dragnet-solutions.com"
_BOUNCE_SUBJECT_PREFIXES = ("undeliverable:", "delivery has failed", "mail delivery failed")


def is_system_bounce_notification(mirror: HelpdeskTicketMirror) -> bool:
    email = (mirror.candidate_email or "").lower()
    subject = (mirror.subject or "").strip().lower()
    return email.endswith(_OUR_DOMAIN_SUFFIX) or subject.startswith(_BOUNCE_SUBJECT_PREFIXES)


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
    if is_system_bounce_notification(mirror):
        # No candidate here — skip classification/grounding/drafting entirely
        # (saves an LLM call, not just a wasted route_to_human).
        mirror.ai_disposition = "no_action"
        session.add(mirror)
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type="tag_only",
            rule="system_bounce_notification",
            reason="Mail-server bounce notification, not a candidate message.",
            executed=False,
        )
        action.executed = execute_ticket_action(zoho_client, mirror, action)
        session.add(action)
        return action

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
    mirror.ai_disposition = _DISPOSITION_BY_ACTION.get(decision.action)
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
    tag_executed = execute_ticket_action(zoho_client, mirror, action)
    action.executed = executed or tag_executed
    session.add(action)
    return action



def _latest_action_time(session: Session, zoho_ticket_id: str):
    """Return the created_at of the most recent HelpdeskAIAction for a ticket, or None."""
    latest = session.exec(
        select(HelpdeskAIAction)
        .where(HelpdeskAIAction.zoho_ticket_id == zoho_ticket_id)
        .order_by(HelpdeskAIAction.created_at.desc())
        .limit(1)
    ).first()
    return latest.created_at if latest else None


def find_pending_tickets(session: Session, limit: int = 50) -> list[HelpdeskTicketMirror]:
    """Open tickets that have never been processed, or were updated by Zoho
    (a new candidate reply) since our last decision on them.

    This is what makes the pipeline self-triggering rather than something we
    run by hand: the sync job upserts mirrors from Zoho, this finds the ones
    that are new or changed, and process_pending_tickets acts on them.
    """
    open_mirrors = session.exec(
        select(HelpdeskTicketMirror)
        .where(HelpdeskTicketMirror.zoho_status == "Open")
        .order_by(HelpdeskTicketMirror.ticket_created_at.desc())
    ).all()

    pending = []
    for mirror in open_mirrors:
        last_action_at = _latest_action_time(session, mirror.zoho_ticket_id)
        if last_action_at is None or (mirror.last_synced_at or mirror.updated_at) > last_action_at:
            pending.append(mirror)
        if len(pending) >= limit:
            break
    return pending


def process_pending_tickets(session: Session, zoho_client, limit: int = 50) -> dict:
    """Run process_ticket on every pending ticket and commit once at the end.

    Returns a per-action tally, e.g. {"draft_reply": 3, "route_to_human": 1}.
    Intended to be called from a scheduled/enqueued job (see
    app/jobs/helpdesk_ai_jobs.py) right after the mirror sync, so new
    candidate messages get a decision without anyone running a script.
    """
    tally: dict[str, int] = {}
    for mirror in find_pending_tickets(session, limit=limit):
        action = process_ticket(session, zoho_client, mirror)
        tally[action.action_type] = tally.get(action.action_type, 0) + 1
    session.commit()
    return tally


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