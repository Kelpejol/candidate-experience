"""Persistent ticket conversations and durable, at-most-once send attempts.

This entry point commits the delivery intent BEFORE touching Zoho. A send with an
unknown outcome is held for reconciliation, never automatically sent again.
SQLite checkpoints and file locks require all workers to share one local host.
"""

import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_conversation_turn import HelpdeskConversationTurn
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_conversation_graph import build_graph, decision, handoff
from app.services.helpdesk_cues import load_registry
from app.services.helpdesk_executor_service import execute_ticket_action
from app.services.helpdesk_thread_context import build_thread_context, clean_thread_text, parse_zoho_time, redact_sensitive_text


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def delivery_mode(mirror: HelpdeskTicketMirror) -> str:
    from app.services.helpdesk_ai_service import _auto_reply_allowed_for, is_conversational_channel

    settings = get_settings()
    if is_conversational_channel(mirror.channel):
        return "whatsapp" if settings.helpdesk_whatsapp_auto_reply_execute else "dry_run"
    if settings.helpdesk_email_auto_reply_execute and _auto_reply_allowed_for(mirror.candidate_email):
        return "email"
    return "draft" if settings.helpdesk_draft_execute else "dry_run"


def _human_owned(assignee: str | None) -> bool:
    allowed = {v.strip() for v in get_settings().helpdesk_automation_assignee_ids.split(",") if v.strip()}
    return bool(assignee and assignee not in allowed)


def _evidence_messages(context) -> list[dict]:
    messages = []
    for message in context.messages:
        role = "candidate" if message.direction == "in" else "officer"
        for index, note in enumerate(message.attachment_notes):
            if note.startswith("[Attachment OCR:") and "\n" in note:
                messages.append({"id": f"{message.id}:ocr:{index}",
                                 "role": "candidate_attachment" if role == "candidate" else "officer",
                                 "text": note.split("\n", 1)[1]})
        messages.append({"id": message.id, "role": role, "text": message.text})
    return messages


def _disposition(action: HelpdeskAIAction, mode: str) -> str:
    if action.action_type in {"ask_clarification", "request_attachment"}:
        return "awaiting_candidate" if mode in {"email", "whatsapp"} and action.executed else "drafted"
    return {"route_to_human": "routed_to_human", "tag_only": "no_action",
            "auto_reply": "auto_replied", "draft_reply": "drafted"}.get(action.action_type, "no_action")


def _mark_uncertain(session, turn, action, mirror):
    turn.delivery_status = "uncertain"
    action.action_type = "route_to_human"
    action.rule = "delivery_outcome_uncertain"
    action.reason = "Zoho may have accepted the reply. Inspect the thread before any resend; automation is held."
    mirror.ai_disposition = "routed_to_human"
    session.add_all([turn, action, mirror])
    session.commit()


def _deliver(session, client, mirror, turn, action):
    if turn.delivery_status == "sending":
        _mark_uncertain(session, turn, action, mirror)
        return action
    if turn.delivery_status != "pending":
        return action
    if not action.draft_text or action.action_type not in {"draft_reply", "ask_clarification", "request_attachment"}:
        turn.delivery_status = "not_required"
    elif turn.delivery_mode == "dry_run":
        turn.delivery_status = "dry_run"
    elif delivery_mode(mirror) != turn.delivery_mode:
        turn.delivery_status = "cancelled"
        action.action_type = "tag_only"
        action.rule = "delivery_mode_changed"
        action.reason = "Execution settings changed after this response was prepared; it was not sent."
    else:
        # Re-read ownership and message identity immediately before any write.
        live = client.get_ticket(mirror.zoho_ticket_id)
        context = build_thread_context(client, mirror.zoho_ticket_id)
        live_email = live.get("email") or (live.get("contact") or {}).get("email")
        if (str(live.get("status", "")).lower() != "open"
                or _human_owned(live.get("assigneeId"))
                or (turn.delivery_mode in {"email", "draft"} and live_email != turn.recipient)
                or context.latest_candidate_id != turn.candidate_message_id
                or context.has_later_public_reply):
            turn.delivery_status = "cancelled"
            action.action_type = "tag_only"
            action.rule = "conversation_superseded"
            action.reason = "A new reply, ownership change or ticket closure superseded the proposed response."
        else:
            turn.delivery_status = "sending"
            turn.updated_at = datetime.utcnow()
            session.add(turn)
            session.commit()
            try:
                if turn.delivery_mode == "whatsapp":
                    result = client.send_whatsapp_reply(ticket_id=mirror.zoho_ticket_id, content=action.draft_text)
                else:
                    send = client.send_reply if turn.delivery_mode == "email" else client.create_draft_reply
                    result = send(ticket_id=mirror.zoho_ticket_id, content=action.draft_text,
                                  from_email_address=turn.sender, to=turn.recipient, content_type="plainText")
                turn.remote_reply_id = str(result["id"]) if isinstance(result, dict) and result.get("id") else None
                turn.delivery_status = "drafted" if turn.delivery_mode == "draft" else "sent"
                action.executed = True
                if action.action_type == "draft_reply" and turn.delivery_mode != "draft":
                    action.action_type = "auto_reply"
            except Exception:
                logging.exception("Zoho conversation delivery has uncertain outcome")
                _mark_uncertain(session, turn, action, mirror)
                return action
    turn.updated_at = datetime.utcnow()
    mirror.ai_disposition = _disposition(action, turn.delivery_mode)
    session.add_all([turn, action, mirror])
    session.commit()
    # A tagging outage must not roll back the durable record of a sent response.
    try:
        tagged = execute_ticket_action(client, mirror, action)
        action.executed = action.executed or tagged
        session.add(action)
        session.commit()
    except Exception:
        session.rollback()
        logging.exception("Conversation action recorded, but Zoho tagging failed")
    return action


def _run_graph(checkpoint_path: Path, conversation_id: str, inputs: dict) -> dict:
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        saver = SqliteSaver(connection)
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": conversation_id}, "recursion_limit": 12}
        existing = graph.get_state(config)
        if existing.values.get("event_id") == inputs["event_id"]:
            return graph.invoke(None, config) if existing.next else existing.values
        return graph.invoke(inputs, config)
    finally:
        connection.close()


def process_conversation_ticket(session: Session, client, mirror: HelpdeskTicketMirror) -> HelpdeskAIAction:
    from app.services.helpdesk_ai_service import has_valid_email_recipient, is_conversational_channel, is_system_bounce_notification

    settings = get_settings()
    started_at = datetime.utcnow()
    path = Path(settings.helpdesk_checkpoint_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = delivery_mode(mirror)
    identity = _hash(settings.zoho_org_id or "default", mirror.zoho_ticket_id)
    conversation_id = _hash(identity, mode)
    # The lock also protects direct invocations that did not come through RQ.
    with FileLock(str(path.parent / f"helpdesk-{identity}.lock"), timeout=0):
        context = build_thread_context(client, mirror.zoho_ticket_id,
                                       max_messages=max(6, min(50, settings.helpdesk_conversation_context_messages)))
        candidate_id = context.latest_candidate_id or "no-candidate"
        event_id = _hash(conversation_id, candidate_id)
        existing = session.get(HelpdeskConversationTurn, event_id)
        if existing:
            return _deliver(session, client, mirror, existing, session.get(HelpdeskAIAction, existing.action_id))

        history_query = select(HelpdeskConversationTurn).join(
            HelpdeskAIAction, HelpdeskAIAction.id == HelpdeskConversationTurn.action_id
        ).where(HelpdeskAIAction.zoho_ticket_id == mirror.zoho_ticket_id)
        history_query = history_query.where(
            HelpdeskConversationTurn.delivery_mode == "dry_run" if mode == "dry_run"
            else HelpdeskConversationTurn.delivery_mode != "dry_run"
        )
        history = session.exec(history_query.order_by(HelpdeskConversationTurn.created_at.desc())).all()
        if mode != "dry_run":
            prior_event = next((t for t in history if t.candidate_message_id == candidate_id), None)
            if prior_event:
                return _deliver(session, client, mirror, prior_event, session.get(HelpdeskAIAction, prior_event.action_id))
        for old in history:
            if old.delivery_status == "pending":
                old.delivery_status = "cancelled"
                old_action = session.get(HelpdeskAIAction, old.action_id)
                old_action.action_type = "tag_only"
                old_action.rule = "conversation_superseded"
                old_action.reason = "A newer candidate event superseded this undelivered response."
                session.add_all([old, old_action])
            elif old.delivery_status == "sending":
                _mark_uncertain(session, old, session.get(HelpdeskAIAction, old.action_id), mirror)
        session.commit()
        previous = history[0] if history else None
        prior_action = session.get(HelpdeskAIAction, previous.action_id) if previous else None
        automated_ids = {t.remote_reply_id for t in history if t.remote_reply_id}
        automated_text = {clean_thread_text(a.draft_text) for t in history
                          if t.delivery_status == "sent"
                          and (a := session.get(HelpdeskAIAction, t.action_id)) and a.draft_text}
        resume_turn = next((t for t in history if session.get(HelpdeskAIAction, t.action_id).rule == "conversation_resumed"), None)
        resumed_at = resume_turn.created_at if resume_turn else None
        manual_reply = any(
            m.direction == "out" and m.id not in automated_ids and m.text not in automated_text
            and (resumed_at is None or parse_zoho_time(m.created_time) > resumed_at)
            for m in context.messages
        )
        if is_system_bounce_notification(mirror):
            output = {"decision": decision("tag_only", "system_bounce_notification", "Not a candidate support message.")}
        elif previous and (any(t.delivery_status == "uncertain" for t in history)
                           or (prior_action and prior_action.action_type == "route_to_human")):
            output = handoff("conversation_human_hold", "This conversation has been handed to an officer; automation remains paused.")
        elif _human_owned(mirror.zoho_assignee_id) or context.has_later_public_reply or manual_reply:
            output = {"decision": decision("tag_only", "conversation_human_owned", "An officer owns or has already answered this message.")}
        elif not context.has_readable_candidate_content:
            output = handoff("empty_or_unreadable_candidate_message", "No readable candidate text or attachment content was available.")
        else:
            try:
                registry = load_registry(settings.helpdesk_cue_registry_path)
                # Only approved, current cues may reach the model.
                registry = registry.model_copy(update={"cues": registry.approved()})
                inputs = {
                    "event_id": event_id, "latest_id": candidate_id,
                    "latest_text": context.latest_candidate_text,
                    "subject": redact_sensitive_text(mirror.subject or ""),
                    "messages": _evidence_messages(context),
                    "attachment_notes": context.attachment_notes,
                    "sentiment": mirror.sentiment, "channel": mirror.channel,
                    "candidate_name": (mirror.candidate_name or "").split(" ")[0] or None,
                    "registry": registry.model_dump(mode="json"),
                }
                graph_id = _hash(conversation_id, resume_turn.id) if resume_turn else conversation_id
                output = _run_graph(path, graph_id, inputs)
            except Exception:
                logging.exception("Conversation graph could not run")
                output = handoff("conversation_runtime_failed", "Conversation infrastructure failed; officer review needed.")
        chosen = output["decision"]
        classification = output.get("understanding", {}).get("classification", {})
        action = HelpdeskAIAction(
            zoho_ticket_id=mirror.zoho_ticket_id, action_type=chosen["action"],
            rule=chosen["rule"], reason=chosen["reason"], draft_text=output.get("reply"),
            issue_category=classification.get("issue_category"),
            tool_name=output.get("resolution", {}).get("tool"),
            confidence_label=classification.get("confidence_label"),
            sensitivity_detected=classification.get("sensitivity_detected", False),
            grounding_status=output.get("grounding_status"),
            created_at=started_at,
        )
        if action.action_type == "route_to_human" and output.get("understanding"):
            understanding = output["understanding"]
            details = ["Issue: " + understanding["issue"],
                       "Missing: " + ", ".join(understanding.get("missing", []))]
            details.extend(o["kind"] + ": " + o["value"] for o in output.get("observations", [])[-12:])
            action.reason += "\n" + "\n".join(details)
        if action.draft_text and not is_conversational_channel(mirror.channel) and not has_valid_email_recipient(mirror.candidate_email):
            action.action_type = "route_to_human"
            action.rule = "missing_candidate_email"
            action.reason = "No valid candidate email is available."
        turn = HelpdeskConversationTurn(
            id=event_id, conversation_id=conversation_id, candidate_message_id=candidate_id,
            action_id=action.id, delivery_mode=mode, recipient=mirror.candidate_email,
            sender=settings.helpdesk_from_email,
            created_at=started_at,
        )
        mirror.tool_name = action.tool_name
        mirror.campaign_name = None
        mirror.issue_category = action.issue_category
        mirror.ai_disposition = _disposition(action, mode)
        session.add_all([action, turn, mirror])
        session.commit()
        return _deliver(session, client, mirror, turn, action)
