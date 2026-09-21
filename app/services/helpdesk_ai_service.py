from sqlmodel import Session, select
import logging
import re
from typing import Protocol


from app.core.config import get_settings
from app.core.vocabulary import is_tool_allowed
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.campaign_scope_service import resolve_campaign, tool_name_to_scope
from app.services.helpdesk_classifier import classify_ticket
from app.services.helpdesk_decision import (
    TicketDecision,
    decide_ticket_action,
    detect_human_request,
)
from app.services.helpdesk_draft_service import generate_draft_reply, generate_whatsapp_reply
from app.services.helpdesk_executor_service import execute_ticket_action
from app.services.helpdesk_kb_service import GroundingResult, retrieve_grounding
from app.services.helpdesk_thread_context import build_thread_context

MEDIUM_CONFIDENCE_STRONG_GROUNDING_DISTANCE = 0.32

# Decision action -> mirror.ai_disposition value (plan doc vocabulary).
_DISPOSITION_BY_ACTION = {
    "draft_reply": "drafted",
    "auto_reply": "auto_replied",
    "route_to_human": "routed_to_human",
    "tag_only": "no_action",
}

# Zoho's raw `channel` values are integration-specific. Email is reliably
# "Email", but social/WhatsApp integrations may arrive as "WhatsApp", "Chat",
# or a close variant. Keep the raw value on the mirror for audit/UI; normalize
# only where behavior diverges.
_CONVERSATIONAL_CHANNEL_KEYS = {
    "whatsapp",
    "whatsappbusiness",
    "chat",
    "im",
    "instantmessaging",
}

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_conversational_channel(channel: str | None) -> bool:
    key = re.sub(r"[^a-z0-9]+", "", (channel or "").lower())
    return key in _CONVERSATIONAL_CHANNEL_KEYS


def has_valid_email_recipient(candidate_email: str | None) -> bool:
    return bool(candidate_email and _EMAIL_PATTERN.match(candidate_email.strip()))


# Mail-server bounce notifications (mailer-daemon / MS Exchange / Gmail NDRs)
# land in Zoho as regular tickets from a bulk campaign send with bad addresses.
# They are not candidates and burn a classifier call (and, worse, a drafted
# reply to a mail daemon) for nothing — filtered out before any LLM call.
# Three independent signals, any one is enough:
#   1. the "from" is on our OWN domain (a real candidate never emails from
#      dragnet-solutions.com — this is our own send bouncing back), OR
#   2. the sender is a mail daemon (mailer-daemon@ / postmaster@ — any domain;
#      this is what caught a Gmail NDR that evaded the domain + subject checks), OR
#   3. the subject carries a classic auto-generated NDR phrase.
_OUR_DOMAIN_SUFFIX = "@dragnet-solutions.com"
_BOUNCE_SENDER_LOCALPARTS = ("mailer-daemon", "postmaster")
_BOUNCE_SUBJECT_MARKERS = (
    "undeliverable",
    "delivery has failed",
    "mail delivery failed",
    "delivery status notification",
    "returned mail",
    "undelivered mail",
    "failure notice",
)


def is_system_bounce_notification(mirror: HelpdeskTicketMirror) -> bool:
    email = (mirror.candidate_email or "").strip().lower()
    subject = (mirror.subject or "").strip().lower()
    local_part = email.split("@", 1)[0] if "@" in email else email

    if email.endswith(_OUR_DOMAIN_SUFFIX):
        return True
    if local_part in _BOUNCE_SENDER_LOCALPARTS:
        return True
    return any(marker in subject for marker in _BOUNCE_SUBJECT_MARKERS)


def apply_grounding_gate(
    decision: TicketDecision,
    grounding: GroundingResult,
    confidence_label: str | None = None,
) -> tuple[TicketDecision, str]:
    """Gate a draft_reply decision on KB coverage.

    Returns (possibly overridden decision, grounding_status). Pure logic —
    the retrieval itself happens in the caller — so the guarantee "no KB
    coverage means no draft" is unit-testable without a network.
    """
    if grounding.grounded:
        if (
            confidence_label == "medium"
            and (
                grounding.best_distance is None
                or grounding.best_distance > MEDIUM_CONFIDENCE_STRONG_GROUNDING_DISTANCE
            )
        ):
            best = (
                f"{grounding.best_distance:.2f}"
                if grounding.best_distance is not None
                else "n/a"
            )
            return (
                TicketDecision(
                    action="route_to_human",
                    rule="medium_confidence_needs_strong_grounding",
                    reason=(
                        "Classifier confidence was medium and KB grounding was not "
                        f"strong enough (best distance {best}); not drafting."
                    ),
                ),
                "weak",
            )
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


def _reply_subject_for(mirror: HelpdeskTicketMirror) -> str | None:
    """What we WANT Zoho to use as the outgoing reply's subject: the same
    "Re:[## <ticketNumber> ##] <subject>" tag its own replies carry, so a
    candidate's follow-up threads back into this ticket instead of opening
    a new one.

    NOT currently wired to send_reply/create_draft_reply: confirmed
    2026-09-17 that Zoho's real API rejects a `subject` field outright on
    both endpoints ("An extra parameter 'subject' is found", HTTP 422) —
    passing it broke every live send until reverted. Kept here, unused, as
    the one part of that attempt that was actually correct (the tag format
    itself, confirmed against a real officer reply) for whoever solves the
    delivery mechanism — see docs/blocked-items-and-helpdesk-plan.md."""
    if not mirror.ticket_number or not mirror.subject:
        return None
    return f"Re:[## {mirror.ticket_number} ##] {mirror.subject}"


def _auto_reply_allowed_for(candidate_email: str | None) -> bool:
    """Gate for helpdesk_email_auto_reply_execute's allowlist.

    Empty allowlist (the default) means no restriction — every candidate is
    eligible, same as before this setting existed. A non-empty allowlist
    restricts auto-send to just those addresses, so a real test address can
    get the live auto-send experience before it's trusted for everyone.
    """
    raw = get_settings().helpdesk_email_auto_reply_test_emails
    if not raw.strip():
        return True
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return bool(candidate_email) and candidate_email.strip().lower() in allowed


def _resolve_kb_scopes(session: Session, classification) -> tuple[str | None, str | None]:
    """Turn what the classifier read off the ticket into KB scope tags.

    Same 3-tier design as the voice KB — general, tool (FOT/Test Haven/
    Scholastica), and campaign — except here the classifier reads tool/
    campaign off the ticket's own text rather than a caller stating them,
    since there's no live conversation to ask. Either can come back None,
    which just means the retrieval falls back to general-only content:
    a hallucinated or unmatched name must never widen the search to the
    wrong tool's answers, only narrow it to a real one.
    """
    tool_scope = None
    if classification.tool_name and is_tool_allowed(classification.tool_name):
        tool_scope = tool_name_to_scope(classification.tool_name)

    campaign_scope = None
    if classification.campaign_name:
        resolved = resolve_campaign(session, classification.campaign_name)
        if resolved["status"] == "found":
            campaign_scope = resolved["scope"]

    return tool_scope, campaign_scope


def process_ticket(session: Session, zoho_client, mirror: HelpdeskTicketMirror) -> HelpdeskAIAction:
    """Classify a mirrored ticket, decide, generate a draft, and record it all.

    Caller commits. With every execute flag off this is a full dry run —
    the generated reply lands in HelpdeskAIAction.draft_text either way, so
    quality can always be reviewed without touching Zoho. When a flag is on,
    an email reply is written to Zoho one of two ways (mutually exclusive,
    auto-reply wins if both are set): settings.helpdesk_draft_execute saves
    an unsent draft an officer must approve and send; settings.
    helpdesk_email_auto_reply_execute sends it immediately with no review.
    """
    if get_settings().helpdesk_conversation_enabled:
        from app.services.helpdesk_conversation_service import process_conversation_ticket

        return process_conversation_ticket(session, zoho_client, mirror)

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

    is_whatsapp = is_conversational_channel(mirror.channel)
    thread_context = build_thread_context(
        zoho_client,
        mirror.zoho_ticket_id,
        conversational=is_whatsapp,
    )
    body = thread_context.text

    if not thread_context.has_readable_candidate_content:
        mirror.ai_disposition = "routed_to_human"
        session.add(mirror)
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type="route_to_human",
            rule="empty_or_unreadable_candidate_message",
            reason="No readable candidate message or OCR text was available; a human should inspect the ticket and attachments.",
            executed=False,
        )
        action.executed = execute_ticket_action(zoho_client, mirror, action)
        session.add(action)
        return action

    try:
        classification = classify_ticket(subject=mirror.subject or "", body=body)
    except Exception:
        # An unparseable/malformed LLM response must still produce a decision.
        # Left uncaught, this ticket has no HelpdeskAIAction row, so
        # find_pending_tickets keeps re-selecting it every tick forever — a
        # silent, invisible failure mode with no human ever alerted. Routing
        # to a human here both surfaces the ticket and stops the loop.
        logging.exception(
            "Ticket classification failed for %s; routing to a human",
            mirror.zoho_ticket_id,
        )
        decision = TicketDecision(
            action="route_to_human",
            rule="classifier_unparseable",
            reason="Could not classify this ticket (malformed model output); escalating.",
        )
        mirror.ai_disposition = _DISPOSITION_BY_ACTION.get(decision.action)
        session.add(mirror)
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id,
            action_type=decision.action,
            rule=decision.rule,
            reason=decision.reason,
            executed=False,
        )
        action.executed = execute_ticket_action(zoho_client, mirror, action)
        session.add(action)
        return action

    # A candidate explicitly asking for a person always escalates, even if
    # the question would otherwise be answerable — checked before the shared
    # decision engine so it short-circuits without spending an embed/grounding
    # call on a message that's escalating regardless.
    latest_candidate_text = thread_context.latest_candidate_text or body
    human_request = detect_human_request(
        f"{mirror.subject or ''} {latest_candidate_text or ''}"
    )
    if human_request:
        decision = TicketDecision(
            action="route_to_human",
            rule="candidate_requested_human",
            reason=f'Candidate asked for a person ("{human_request}").',
        )
    else:
        decision = decide_ticket_action(
            classification,
            subject=mirror.subject or "",
            zoho_sentiment=mirror.sentiment,
            body=latest_candidate_text or "",
        )

    # Grounding gate: a draft may only happen when the KB actually covers
    # the question. Retrieval failure (e.g. KB never synced) counts as
    # ungrounded — degrade to a human, never crash ticket processing.
    grounding_status = None
    grounding = None
    if decision.action == "draft_reply":
        tool_scope, campaign_scope = _resolve_kb_scopes(session, classification)
        try:
            grounding = retrieve_grounding(
                body or mirror.subject or "",
                tool_scope=tool_scope,
                campaign_scope=campaign_scope,
            )
        except Exception:
            grounding = GroundingResult(grounded=False, best_distance=None, chunks=[])
        decision, grounding_status = apply_grounding_gate(
            decision, grounding, classification.confidence_label
        )

    # Reply generation: grounded draft_reply decisions get a reply written
    # from the retrieved chunks. The model may still escalate mid-generation
    # if the chunks don't truly answer — that flips the decision to a human.
    # WhatsApp and email diverge here: WhatsApp has no draft step (it's
    # conversational, real-time — see decide_ticket_action's WhatsApp note in
    # the module docstring history / plan doc), so a grounded answer is
    # labeled "auto_reply" and sent directly rather than left as a draft.
    draft_text = None
    executed = False
    if decision.action == "draft_reply" and grounding is not None:
        first_name = (mirror.candidate_name or "").split(" ")[0] or None
        draft_failed = False
        try:
            if is_whatsapp:
                draft_text = generate_whatsapp_reply(
                    candidate_message=body,
                    kb_chunks=grounding.chunks,
                    candidate_name=first_name,
                )
            else:
                draft_text = generate_draft_reply(
                    subject=mirror.subject or "",
                    candidate_message=body,
                    kb_chunks=grounding.chunks,
                    candidate_name=first_name,
                )
        except Exception:
            # Same degradation rule as retrieval above: a gateway failure must
            # route this ticket to a human, not abort processing.
            logging.exception(
                "Reply generation failed for ticket %s; routing to a human",
                mirror.zoho_ticket_id,
            )
            draft_text = None
            draft_failed = True

        if draft_failed:
            # Keep the reason honest — this was an outage, not a judgment about
            # the KB. Officers triage these very differently.
            decision = TicketDecision(
                action="route_to_human",
                rule="draft_writer_unavailable",
                reason="Reply generation failed (inference gateway error); escalating.",
            )
        elif draft_text is None:
            decision = TicketDecision(
                action="route_to_human",
                rule="draft_writer_escalated",
                reason="Reply-writer judged the KB excerpts insufficient to answer; escalating.",
            )
        elif is_whatsapp:
            decision = TicketDecision(
                action="auto_reply", rule=decision.rule, reason=decision.reason
            )
            if get_settings().helpdesk_whatsapp_auto_reply_execute:
                zoho_client.send_whatsapp_reply(
                    ticket_id=mirror.zoho_ticket_id, content=draft_text
                )
                executed = True
        elif not has_valid_email_recipient(mirror.candidate_email):
            decision = TicketDecision(
                action="route_to_human",
                rule="missing_candidate_email",
                reason="No valid candidate email is available for this ticket; escalating instead of writing a reply.",
            )
        elif get_settings().helpdesk_email_auto_reply_execute and _auto_reply_allowed_for(
            mirror.candidate_email
        ):
            # Full-operation mode: skip the draft step, send immediately.
            # Takes priority over helpdesk_draft_execute — see config.py.
            decision = TicketDecision(
                action="auto_reply", rule=decision.rule, reason=decision.reason
            )
            zoho_client.send_reply(
                ticket_id=mirror.zoho_ticket_id,
                content=draft_text,
                from_email_address=get_settings().helpdesk_from_email,
                to=mirror.candidate_email or "",
                content_type="plainText",
            )
            executed = True
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
        delivery_pending = False
        if get_settings().helpdesk_conversation_enabled:
            from app.models.helpdesk_conversation_turn import HelpdeskConversationTurn

            delivery_pending = session.exec(
                select(HelpdeskConversationTurn.id)
                .join(HelpdeskAIAction, HelpdeskAIAction.id == HelpdeskConversationTurn.action_id)
                .where(HelpdeskAIAction.zoho_ticket_id == mirror.zoho_ticket_id)
                .where(HelpdeskConversationTurn.delivery_status.in_(["pending", "sending"]))
                .limit(1)
            ).first() is not None
        if last_action_at is None or _changed_since(mirror, last_action_at) or delivery_pending:
            pending.append(mirror)
        if len(pending) >= limit:
            break
    return pending


def _changed_since(mirror: HelpdeskTicketMirror, last_action_at) -> bool:
    """Whether the TICKET changed since we last decided on it.

    Compares Zoho's own modifiedTime, not our last_synced_at. last_synced_at is
    our clock and is re-stamped on every sync, so using it here would mark every
    open ticket pending on every cron tick — re-classifying, re-drafting and
    re-tagging the same ticket indefinitely.

    When Zoho gives us no modifiedTime we treat the ticket as unchanged: a
    ticket we've already acted on should stay put until there's real evidence
    it moved. Genuinely new tickets are caught by the `last_action_at is None`
    branch in the caller.
    """
    if mirror.zoho_modified_at is None:
        return False
    return mirror.zoho_modified_at > last_action_at


class TicketProcessingLock(Protocol):
    def acquire(self, zoho_ticket_id: str) -> str | None: ...
    def release(self, zoho_ticket_id: str, token: str) -> None: ...


def process_pending_tickets(
    session: Session,
    zoho_client,
    limit: int = 50,
    ticket_lock: TicketProcessingLock | None = None,
) -> dict:
    """Run process_ticket on every pending ticket, committing each one.

    Returns a per-action tally, e.g. {"draft_reply": 3, "route_to_human": 1},
    plus an "error" count for tickets that failed. Intended to be called from a
    scheduled/enqueued job (see app/jobs/helpdesk_ai_jobs.py) right after the
    mirror sync, so new candidate messages get a decision without anyone
    running a script.

    Each ticket is isolated and committed on its own. Two reasons this matters:
    a single failing ticket (LLM timeout, odd data, a Zoho error) must not stop
    the rest of the batch; and process_ticket may already have written a draft
    or tags to the real Zoho ticket, so a batch-wide rollback would leave our
    audit log denying writes that actually happened.
    """
    tally: dict[str, int] = {}
    for mirror in find_pending_tickets(session, limit=limit):
        lock_token = None
        if ticket_lock is not None:
            lock_token = ticket_lock.acquire(mirror.zoho_ticket_id)
            if lock_token is None:
                tally["skipped_locked"] = tally.get("skipped_locked", 0) + 1
                continue

        try:
            action = process_ticket(session, zoho_client, mirror)
            session.commit()
            tally[action.action_type] = tally.get(action.action_type, 0) + 1
        except Exception:
            session.rollback()
            tally["error"] = tally.get("error", 0) + 1
            logging.exception(
                "Helpdesk processing failed for ticket %s", mirror.zoho_ticket_id
            )
        finally:
            if ticket_lock is not None and lock_token is not None:
                ticket_lock.release(mirror.zoho_ticket_id, lock_token)
    return tally


def get_latest_candidate_message(zoho_client, zoho_ticket_id: str) -> str:
    """Return the candidate thread context for backward-compatible callers.

    `process_ticket` uses `build_thread_context` directly so it can pass the
    channel-aware conversational flag and inspect whether content was readable.
    This wrapper remains for scripts/tests that imported the older helper.
    """
    return build_thread_context(zoho_client, zoho_ticket_id).text
