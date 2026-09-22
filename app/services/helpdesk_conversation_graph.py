"""LangGraph reasoning flow. External delivery is outside replayable graph nodes."""

import hashlib
import json
import logging
import re

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.services import helpdesk_conversation_llm as llm
from app.services.campaign_scope_service import tool_name_to_scope
from app.services.helpdesk_conversation_models import ConversationState, Understanding
from app.services.helpdesk_cues import CueRegistry, normalize, resolve_tools
from app.services.helpdesk_decision import TicketDecision, decide_ticket_action, detect_human_request
from app.services.helpdesk_draft_service import generate_draft_reply, generate_whatsapp_reply
from app.services.helpdesk_kb_service import retrieve_grounding


def decision(action: str, rule: str, reason: str) -> dict:
    return TicketDecision(action=action, rule=rule, reason=reason).model_dump()


def handoff(rule: str, reason: str) -> dict:
    return {"decision": decision("route_to_human", rule, reason), "reply": None}


def context_payload(state: ConversationState) -> dict:
    return {key: state.get(key) for key in (
        "subject", "messages", "latest_id", "latest_text", "attachment_notes",
        "resolution", "observations", "questions", "previous_issue", "registry",
    )}


def prepare(state: ConversationState) -> dict:
    registry = CueRegistry.model_validate(state["registry"])
    messages = list(state["messages"])
    previous = state.get("resolution", {})
    if previous.get("source_id") and previous["source_id"] not in {m["id"] for m in messages}:
        messages.insert(0, {"id": previous["source_id"], "role": "candidate",
                            "text": previous["tool"] if previous.get("confirmed") else previous["evidence"]})
    resolution = resolve_tools(messages, state["latest_id"], state["subject"], registry)
    questions = state.get("questions", [])
    if questions and normalize(state["latest_text"]) in {"yes", "yes correct", "correct", "that one", "yes that one"}:
        last = questions[-1]
        confirmation = last.get("confirmation")
        actually_asked = any(m["role"] == "officer" and normalize(last["text"]) in normalize(m["text"]) for m in state["messages"])
        if confirmation and actually_asked:
            # Confirm only an approved, unconditional mapping from the current registry.
            approved = next((c for c in registry.approved() if c.id == confirmation["id"] and c.exclusive and not c.conditions), None)
            tools = approved.tools if approved else []
            if confirmation["id"].startswith("tool:"):
                from app.core.vocabulary import ALLOWED_TOOLS
                name = confirmation["id"][5:]
                tools = [name] if name in ALLOWED_TOOLS else []
            if len(tools) == 1:
                resolution = {"tool": tools[0], "options": tools, "source_id": state["latest_id"],
                              "evidence": state["latest_text"], "conflict": False, "confirmed": True}
    return {
        "resolution": resolution,
        "decision": {}, "reply": None, "chunks": [], "grounding_status": None,
        "understanding": {}, "failure": None,
    }


def understand(state: ConversationState) -> dict:
    human_request = detect_human_request(state["latest_text"])
    if human_request:
        return handoff("candidate_requested_human", "Candidate requested an officer.")
    try:
        plan = llm.understand(context_payload(state))
        plan.classification = plan.classification.model_copy(update={
            "tool_name": state["resolution"]["tool"], "campaign_name": None,
        })
        sources = {m["id"]: m["text"] for m in state["messages"] if m["role"] in {"candidate", "candidate_attachment"}}
        observations = list(state.get("observations", []))
        known = {(o["kind"], normalize(o["quote"])) for o in observations}
        for observation in plan.observations:
            if observation.quote not in sources.get(observation.source_id, ""):
                raise ValueError("Observation is not supported by a candidate message")
            key = (observation.kind, normalize(observation.quote))
            if key not in known:
                observations.append(observation.model_dump())
                known.add(key)
        # Preserve bounded evidence, not an invented free-form conversation history.
        return {"understanding": plan.model_dump(), "observations": observations[-100:]}
    except Exception:
        logging.exception("Conversation understanding failed")
        return handoff("conversation_understanding_failed", "Could not reliably interpret this conversation; officer review needed.")


def question(state: ConversationState, plan: Understanding, *, need_tool: bool) -> dict:
    """Ask the model's own composed clarifying question, as-is.

    The model already receives `resolution` (tool/options/suggestions
    narrowed by cues) as part of its input, and reasons over it, real KB
    context (via its own tool calls in understand()), and the conversation
    itself to decide what to actually ask and how to phrase it -- this
    function no longer overwrites that with a fixed template. What it still
    enforces, unconditionally: this must be a real question the model
    actually wants to ask (useful_next_step/purpose set), it must not solicit
    a password/OTP, and it must not repeat an approach that already failed to
    make progress (the fingerprint/stall check below).
    """
    resolution = state["resolution"]
    text = plan.next_question.strip()
    target = normalize(plan.question_target)
    strategy = plan.question_strategy
    confirmation = None
    if need_tool:
        target = "platform"
        suggestions = resolution.get("suggestions", [])
        if len(suggestions) == 1:
            # Bookkeeping only -- lets prepare() recognize a plain "yes" as
            # confirming this suggestion later, regardless of how the model
            # actually phrased the question.
            confirmation = suggestions[0]
    if not plan.useful_next_step or not text or not target or not plan.question_purpose:
        return handoff("clarification_no_useful_next_step", plan.reason)
    if re.search(r"(?:send|provide|share|tell|enter).{0,30}(?:your password|your otp|verification code)", text, re.I):
        return handoff("clarification_invalid_request", "Clarification requested sensitive information.")
    evidence = sorted({normalize(o["quote"]) for o in state.get("observations", [])})
    entity = {k: resolution[k] for k in ("tool", "options", "conflict")}
    fingerprint = hashlib.sha256(json.dumps([entity, evidence], sort_keys=True).encode()).hexdigest()
    asked = list(state.get("questions", []))
    if any(q["target"] == target and q["strategy"] == strategy and q["evidence"] == fingerprint for q in asked):
        return handoff("clarification_stalled", "The same clarification approach would repeat without new evidence. " + plan.reason)
    asked.append({"target": target, "strategy": strategy, "evidence": fingerprint,
                  "text": text, "purpose": plan.question_purpose, "event_id": state["event_id"], "confirmation": confirmation})
    return {"decision": decision("request_attachment" if strategy == "screenshot" else "ask_clarification",
                                 "clarification_needed", plan.question_purpose),
            "reply": text, "questions": asked[-100:], "previous_issue": plan.issue}


def choose(state: ConversationState) -> dict:
    if state.get("decision"):
        return {}
    plan = Understanding.model_validate(state["understanding"])
    classification = plan.classification.model_copy(update={"tool_name": state["resolution"]["tool"], "campaign_name": None})
    base = decide_ticket_action(classification, subject=state["subject"],
                                zoho_sentiment=state.get("sentiment"), body=state["latest_text"])
    if base.action == "route_to_human" and base.rule != "low_confidence":
        return {"decision": base.model_dump()}
    if base.action == "tag_only" and not state["resolution"].get("confirmed"):
        return {"decision": base.model_dump()}
    if plan.escalate:
        return handoff("conversation_needs_officer", plan.reason)
    requires_tool = plan.needs_tool or classification.issue_category == "technical_issue"
    need_tool = requires_tool and not state["resolution"]["tool"]
    if need_tool or plan.missing or state["resolution"]["conflict"]:
        return question(state, plan, need_tool=need_tool or state["resolution"]["conflict"])
    if not plan.useful_next_step:
        return handoff("conversation_no_useful_next_step", plan.reason)
    return {"decision": decision("draft_reply", "conversation_answer", plan.reason), "previous_issue": plan.issue}


def retrieve(state: ConversationState) -> dict:
    try:
        plan = Understanding.model_validate(state["understanding"])
        tool = state["resolution"]["tool"]
        scope = tool_name_to_scope(tool) if tool else None
        query = plan.issue + "\n" + state["latest_text"]
        grounding = retrieve_grounding(query, tool_scope=scope)
        allowed = {"general", scope} if scope else {"general"}
        threshold = get_settings().kb_grounding_threshold
        chunks = [c for c in grounding.chunks if c.get("scope") in allowed
                  and c.get("distance", 1.0) <= threshold]
        if not grounding.grounded or not chunks:
            return {**handoff("kb_grounding_missing", "No applicable knowledge was found for this request."), "grounding_status": "missing"}
        if (plan.needs_tool or plan.classification.issue_category == "technical_issue") and not any(c.get("scope") == scope for c in chunks):
            return {**handoff("tool_knowledge_missing", "Only general knowledge was found for a platform-specific issue."), "grounding_status": "missing"}
        return {"chunks": chunks, "grounding_status": "grounded"}
    except Exception:
        logging.exception("Conversation KB retrieval failed")
        return {**handoff("kb_unavailable", "Knowledge retrieval failed; officer review needed."), "grounding_status": "unavailable"}


def compose(state: ConversationState) -> dict:
    try:
        from app.services.helpdesk_ai_service import is_conversational_channel

        context = json.dumps(context_payload(state), ensure_ascii=True)
        if is_conversational_channel(state["channel"]):
            reply = generate_whatsapp_reply(context, state["chunks"], state.get("candidate_name"))
        else:
            reply = generate_draft_reply(state["subject"], context, state["chunks"], state.get("candidate_name"))
        if not reply or not reply.strip():
            return handoff("draft_writer_escalated", "The reply writer could not answer from the applicable knowledge.")
        return {"reply": reply}
    except Exception:
        logging.exception("Conversation reply generation failed")
        return handoff("draft_writer_unavailable", "Reply generation failed; officer review needed.")


def review(state: ConversationState) -> dict:
    try:
        result = llm.review({**context_payload(state), "understanding": state["understanding"],
                             "kb_chunks": state["chunks"], "reply": state["reply"]})
        if not (result.supported and result.addresses_request and result.applicable) or result.repeats_failed_fix:
            return handoff("answer_review_failed", result.reason)
        return {}
    except Exception:
        logging.exception("Conversation answer review failed")
        return handoff("answer_review_unavailable", "Could not validate the proposed answer; officer review needed.")


def build_graph(checkpointer):
    graph = StateGraph(ConversationState)
    for name, node in (("prepare", prepare), ("understand", understand), ("choose", choose),
                       ("retrieve", retrieve), ("compose", compose), ("review", review)):
        graph.add_node(name, node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "understand")
    graph.add_edge("understand", "choose")
    graph.add_conditional_edges("choose", lambda s: "retrieve" if s["decision"]["action"] == "draft_reply" else END)
    graph.add_conditional_edges("retrieve", lambda s: "compose" if s["decision"]["action"] == "draft_reply" else END)
    graph.add_conditional_edges("compose", lambda s: "review" if s["decision"]["action"] == "draft_reply" else END)
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
