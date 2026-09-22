from datetime import datetime
import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlmodel import select

from app.core.config import get_settings
from app.models.helpdesk_ai_action import HelpdeskAIAction
from app.models.helpdesk_conversation_turn import HelpdeskConversationTurn
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services import helpdesk_conversation_graph as graph_module
from app.services import helpdesk_conversation_service as service
from app.services.helpdesk_classifier import TicketClassification
from app.services.helpdesk_conversation_models import AnswerReview, Understanding
from app.services.helpdesk_cues import CueRegistry, resolve_tools
from app.services.helpdesk_kb_service import GroundingResult


def reviewed_registry(data):
    registry = CueRegistry.model_validate(data)
    for cue in registry.cues:
        if cue.status == "approved":
            cue.approved_digest = cue.content_digest()
    return registry


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    for name in ("HELPDESK_TAG_EXECUTE", "HELPDESK_DRAFT_EXECUTE", "HELPDESK_EMAIL_AUTO_REPLY_EXECUTE",
                 "HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE"):
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("HELPDESK_CONVERSATION_ENABLED", "true")
    monkeypatch.setenv("HELPDESK_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_TEST_EMAILS", "")
    monkeypatch.setenv("HELPDESK_AUTOMATION_ASSIGNEE_IDS", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def plan(**overrides):
    values = dict(
        classification=TicketClassification(issue_category="technical_issue", tool_name="Test Haven",
            sensitivity_detected=False, confidence_label="low", reason="Ambiguous platform"),
        issue="Cannot log in to assessment", needs_tool=True,
        next_question="Which platform?", question_target="platform", question_purpose="Identify the platform",
        useful_next_step=True, reason="Need context",
    )
    values.update(overrides)
    return Understanding(**values)


def inputs(text="I cannot log in", event="1", **overrides):
    data = dict(event_id=event, latest_id=event, latest_text=text, subject="Login issue",
                messages=[{"id": event, "role": "candidate", "text": text}],
                attachment_notes=[], sentiment=None, channel="Email", candidate_name="Ada",
                registry={"version": "1", "cues": []})
    data.update(overrides)
    return data


@pytest.fixture
def graph():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    yield graph_module.build_graph(SqliteSaver(connection))
    connection.close()


CONFIG = {"configurable": {"thread_id": "test-conversation"}}


def mock_answer(monkeypatch, *, scope="fot", approve=True):
    monkeypatch.setattr(graph_module, "retrieve_grounding", lambda *a, **k: GroundingResult(
        grounded=True, best_distance=0.1,
        chunks=[{"text": "Use the details from your invitation.", "scope": scope, "distance": 0.1}],
    ))
    monkeypatch.setattr(graph_module, "generate_draft_reply", lambda *a, **k: "Use the details from your invitation.")
    monkeypatch.setattr(graph_module.llm, "review", lambda data: AnswerReview(
        supported=approve, addresses_request=True, applicable=True, repeats_failed_fix=False, reason="Reviewed",
    ))


def test_ambiguous_login_asks_instead_of_using_classifier_tool(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    monkeypatch.setattr(graph_module, "retrieve_grounding", lambda *a, **k: pytest.fail("Must clarify before retrieval"))
    result = graph.invoke(inputs(), CONFIG)
    assert result["decision"]["action"] == "ask_clarification"
    assert result["resolution"]["tool"] is None
    # The model's own composed question is used as-is now, not a fixed
    # template listing every tool -- verify it's passed through unmodified.
    assert result["reply"] == plan().next_question


def test_explicit_fot_overrides_wrong_valid_classifier_tool(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch)
    result = graph.invoke(inputs("I cannot access my FOT test"), CONFIG)
    assert result["resolution"]["tool"] == "FOT"
    assert result["decision"]["action"] == "draft_reply"


def test_question_mark_does_not_make_explicit_tool_ambiguous():
    result = resolve_tools(inputs("Why is FOT not loading?")["messages"], "1", "", CueRegistry(version="1"))
    assert result["tool"] == "FOT"


def test_explicit_negated_tool_does_not_conflict_with_positive_correction():
    text = "I am not using FOT, I am using Test Haven"
    result = resolve_tools(inputs(text)["messages"], "1", "FOT login", CueRegistry(version="1"))
    assert result["tool"] == "Test Haven"


def test_officer_options_are_not_candidate_tool_evidence():
    messages = [{"id": "1", "role": "officer", "text": "Are you using Test Haven?"},
                {"id": "2", "role": "candidate", "text": "I am unsure"}]
    assert resolve_tools(messages, "2", "", CueRegistry(version="1"))["tool"] is None


def test_shared_cue_does_not_choose_tool():
    registry = reviewed_registry({"version": "1", "cues": [{
        "id": "sb", "phrase": "Secure Browser", "tools": ["FOT", "Test Haven"],
        "source": "officer", "status": "approved", "approved_by": "Reviewer", "approved_at": "2026-01-01",
    }]})
    result = resolve_tools(inputs("Secure Browser failed")["messages"], "1", "", registry)
    assert result["tool"] is None
    assert result["options"] == ["FOT", "Test Haven"]


def test_unapproved_campaign_cue_never_resolves():
    registry = CueRegistry.model_validate({"version": "1", "cues": [{
        "id": "campaign", "phrase": "Example Client", "tools": ["FOT"],
        "source": "history", "exclusive": True,
    }]})
    assert resolve_tools(inputs("Example Client")["messages"], "1", "", registry)["tool"] is None


def test_approved_campaign_mapping_does_not_require_campaign_kb():
    registry = reviewed_registry({"version": "1", "cues": [{
        "id": "campaign", "phrase": "Example Client", "tools": ["FOT"], "campaign": "Example Client Exam",
        "source": "officer", "exclusive": True, "status": "approved", "approved_by": "Reviewer", "approved_at": "2026-01-01",
    }]})
    assert resolve_tools(inputs("Example Client")["messages"], "1", "", registry)["tool"] == "FOT"


def test_followup_retains_issue_and_answers_from_fot(graph, monkeypatch):
    payloads = []
    def understand(data):
        payloads.append(data)
        return plan()
    monkeypatch.setattr(graph_module.llm, "understand", understand)
    mock_answer(monkeypatch)
    first = graph.invoke(inputs(), CONFIG)
    assert first["decision"]["action"] == "ask_clarification"
    second = graph.invoke(inputs("FOT", "2"), CONFIG)
    assert payloads[-1]["previous_issue"] == "Cannot log in to assessment"
    assert second["resolution"]["tool"] == "FOT"
    assert second["reply"] == "Use the details from your invitation."


def test_tool_is_remembered_beyond_recent_window(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch)
    graph.invoke(inputs("FOT"), CONFIG)
    second = graph.invoke(inputs("Still cannot log in", "2"), CONFIG)
    assert second["resolution"]["tool"] == "FOT"


def test_latest_tool_correction_overrides_subject(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch, scope="test-haven")
    result = graph.invoke(inputs("Actually I am using Test Haven", subject="FOT login"), CONFIG)
    assert result["resolution"]["tool"] == "Test Haven"


def test_conflicting_names_require_confirmation(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    result = graph.invoke(inputs("FOT or Test Haven I am not sure"), CONFIG)
    assert result["decision"]["action"] == "ask_clarification"


def test_more_than_two_clarifications_allowed_with_progress(graph, monkeypatch):
    for index in range(5):
        text = f"Error code {index}"
        monkeypatch.setattr(graph_module.llm, "understand", lambda data, text=text, index=index: plan(
            observations=[{"kind": "error", "value": text, "source_id": str(index), "quote": text}],
        ))
        result = graph.invoke(inputs(text, str(index)), CONFIG)
        assert result["decision"]["action"] == "ask_clarification"
    assert len(result["questions"]) == 5


def test_repeated_question_without_progress_escalates(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    graph.invoke(inputs("I don't know", "1"), CONFIG)
    result = graph.invoke(inputs("I don't know", "2"), CONFIG)
    assert result["decision"]["rule"] == "clarification_stalled"


def test_alternative_screenshot_strategy_is_allowed(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    graph.invoke(inputs(), CONFIG)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan(question_strategy="screenshot"))
    result = graph.invoke(inputs("I don't know", "2"), CONFIG)
    assert result["decision"]["action"] == "request_attachment"


def test_fabricated_observation_fails_closed(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan(
        observations=[{"kind": "error", "value": "camera error", "quote": "camera error", "source_id": "1"}],
    ))
    result = graph.invoke(inputs(), CONFIG)
    assert result["decision"]["rule"] == "conversation_understanding_failed"


@pytest.mark.parametrize("scope", ["test-haven", "general", None])
def test_wrong_or_missing_tool_knowledge_is_not_used(graph, monkeypatch, scope):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch, scope=scope)
    result = graph.invoke(inputs("FOT"), CONFIG)
    assert result["decision"]["action"] == "route_to_human"
    assert result["reply"] is None


def test_answer_review_can_reject_grounded_draft(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch, approve=False)
    result = graph.invoke(inputs("FOT"), CONFIG)
    assert result["decision"]["rule"] == "answer_review_failed"
    assert result["reply"] is None


def test_human_request_does_not_call_model(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: pytest.fail("Must respect human request first"))
    result = graph.invoke(inputs("Please connect me to a human"), CONFIG)
    assert result["decision"]["rule"] == "candidate_requested_human"


class Zoho:
    def __init__(self):
        self.messages = [{"id": "1", "direction": "in", "content": "I cannot log in", "createdTime": "2026-09-19T10:00:00Z"}]
        self.sent = []
        self.assignee = None
        self.status = "Open"
        self.fail_send = False

    def list_ticket_threads(self, ticket_id):
        return {"data": self.messages}

    def get_thread(self, ticket_id, thread_id):
        return next(m for m in self.messages if m["id"] == thread_id)

    def get_ticket(self, ticket_id):
        return {"id": ticket_id, "status": self.status, "assigneeId": self.assignee, "email": "ada@example.com"}

    def send_reply(self, **kwargs):
        self.sent.append(kwargs)
        if self.fail_send:
            raise TimeoutError("Response lost after acceptance")
        return {"id": "out-1"}

    create_draft_reply = send_reply
    send_whatsapp_reply = send_reply


def mirror(session):
    row = HelpdeskTicketMirror(zoho_ticket_id="ticket", channel="Email", subject="Login", candidate_email="ada@example.com", zoho_status="Open")
    session.add(row)
    session.commit()
    return row


def enable_send(monkeypatch):
    monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()


def test_entrypoint_persists_clarification_and_deduplicates(session, monkeypatch):
    from app.services.helpdesk_ai_service import process_ticket
    enable_send(monkeypatch)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    first = process_ticket(session, client, row)
    second = process_ticket(session, client, row)
    assert first.id == second.id
    assert first.action_type == "ask_clarification"
    assert row.ai_disposition == "awaiting_candidate"
    assert len(client.sent) == 1
    assert session.exec(select(HelpdeskConversationTurn)).one().delivery_status == "sent"


def test_checkpoint_survives_new_connection_and_next_candidate_reply(session, monkeypatch):
    enable_send(monkeypatch)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch)
    row = mirror(session)
    client = Zoho()
    service.process_conversation_ticket(session, client, row)
    client.messages.append({"id": "2", "direction": "in", "content": "FOT", "createdTime": "2026-09-19T10:01:00Z"})
    action = service.process_conversation_ticket(session, client, row)
    assert action.action_type == "auto_reply"
    assert action.tool_name == "FOT"
    assert len(client.sent) == 2


def test_uncertain_send_is_never_retried(session, monkeypatch):
    enable_send(monkeypatch)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    client.fail_send = True
    first = service.process_conversation_ticket(session, client, row)
    second = service.process_conversation_ticket(session, client, row)
    assert first.id == second.id
    assert second.rule == "delivery_outcome_uncertain"
    assert len(client.sent) == 1
    client.messages.append({"id": "2", "direction": "in", "content": "FOT", "createdTime": "2026-09-19T10:01:00Z"})
    third = service.process_conversation_ticket(session, client, row)
    assert third.rule == "conversation_human_hold"
    assert len(client.sent) == 1


def test_crash_after_send_intent_requires_reconciliation(session, monkeypatch):
    enable_send(monkeypatch)
    row = mirror(session)
    action = HelpdeskAIAction(zoho_ticket_id="ticket", action_type="ask_clarification", rule="clarification_needed", draft_text="Which tool?")
    turn = HelpdeskConversationTurn(id="intent", conversation_id="conversation", candidate_message_id="1", action_id=action.id, delivery_status="sending", delivery_mode="email")
    session.add_all([action, turn])
    session.commit()
    client = Zoho()
    service._deliver(session, client, row, turn, action)
    assert turn.delivery_status == "uncertain"
    assert client.sent == []


def test_officer_takes_over_during_reasoning(session, monkeypatch):
    enable_send(monkeypatch)
    row = mirror(session)
    client = Zoho()
    def understand(data):
        client.assignee = "officer"
        return plan()
    monkeypatch.setattr(graph_module.llm, "understand", understand)
    action = service.process_conversation_ticket(session, client, row)
    assert action.rule == "conversation_superseded"
    assert client.sent == []


def test_new_candidate_message_cancels_stale_response(session, monkeypatch):
    enable_send(monkeypatch)
    row = mirror(session)
    client = Zoho()
    def understand(data):
        client.messages.append({"id": "2", "direction": "in", "content": "FOT", "createdTime": "2026-09-19T10:01:00Z"})
        return plan()
    monkeypatch.setattr(graph_module.llm, "understand", understand)
    action = service.process_conversation_ticket(session, client, row)
    assert action.rule == "conversation_superseded"
    assert client.sent == []


def test_dry_run_does_not_send_or_poison_live_checkpoint(session, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    dry = service.process_conversation_ticket(session, client, row)
    assert not dry.executed
    assert not client.sent
    enable_send(monkeypatch)
    live = service.process_conversation_ticket(session, client, row)
    assert live.id != dry.id
    assert live.action_type == "ask_clarification"
    assert len(client.sent) == 1


def test_edited_cue_loses_approval():
    registry = reviewed_registry({"version": "1", "cues": [{
        "id": "campaign", "phrase": "Example Client", "tools": ["FOT"],
        "source": "officer", "exclusive": True, "status": "approved", "approved_by": "Reviewer", "approved_at": "2026-01-01",
    }]})
    assert len(registry.approved()) == 1
    registry.cues[0].tools = ["Test Haven"]
    assert registry.approved() == []


def test_typo_suggests_then_explicit_confirmation_resolves(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    mock_answer(monkeypatch, scope="test-haven")
    first = graph.invoke(inputs("I am using Test Havn"), CONFIG)
    assert first["resolution"]["tool"] is None
    # The model's own composed question is used as-is; what matters here is
    # the confirmation bookkeeping below (a single suggestion resolves on a
    # plain "yes"), not the exact wording.
    assert first["reply"] == plan().next_question
    second_inputs = inputs("Yes", "3")
    second_inputs["messages"].insert(0, {"id": "2", "role": "officer", "text": first["reply"]})
    second = graph.invoke(second_inputs, CONFIG)
    assert second["resolution"]["tool"] == "Test Haven"
    assert second["decision"]["action"] == "draft_reply"


def test_yes_does_not_confirm_an_unsent_question(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    graph.invoke(inputs("I am using Test Havn"), CONFIG)
    second = graph.invoke(inputs("Yes", "2"), CONFIG)
    assert second["resolution"]["tool"] is None


def test_generic_question_can_use_general_without_tool(graph, monkeypatch):
    classification = TicketClassification(issue_category="general_enquiry", sensitivity_detected=False,
                                          confidence_label="high", reason="General process question")
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan(classification=classification, needs_tool=False))
    mock_answer(monkeypatch, scope="general")
    result = graph.invoke(inputs("How do I contact support?"), CONFIG)
    assert result["decision"]["action"] == "draft_reply"


def test_technical_issue_cannot_bypass_tool_requirement(graph, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan(needs_tool=False))
    result = graph.invoke(inputs(), CONFIG)
    assert result["decision"]["action"] == "ask_clarification"


def test_sensitive_classification_still_escalates(graph, monkeypatch):
    classification = TicketClassification(issue_category="payment_issue", sensitivity_detected=True,
                                          confidence_label="high", reason="Payment issue")
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan(classification=classification))
    result = graph.invoke(inputs(), CONFIG)
    assert result["decision"]["rule"] == "sensitive_never_automated"


def test_recipient_change_cancels_delivery(session, monkeypatch):
    enable_send(monkeypatch)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    client.get_ticket = lambda tid: {"status": "Open", "email": "changed@example.com"}
    action = service.process_conversation_ticket(session, client, row)
    assert action.rule == "conversation_superseded"
    assert client.sent == []


def test_pending_delivery_is_retried_by_polling(session, monkeypatch):
    from app.services.helpdesk_ai_service import find_pending_tickets
    enable_send(monkeypatch)
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    get_ticket = client.get_ticket
    client.get_ticket = lambda tid: (_ for _ in ()).throw(ConnectionError("Preflight failed"))
    with pytest.raises(ConnectionError):
        service.process_conversation_ticket(session, client, row)
    assert row in find_pending_tickets(session)
    assert session.exec(select(HelpdeskConversationTurn)).one().delivery_status == "pending"
    client.get_ticket = get_ticket
    result = service.process_conversation_ticket(session, client, row)
    assert result.executed
    assert len(client.sent) == 1


def test_unassigned_officer_reply_pauses_automation(session, monkeypatch):
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: pytest.fail("Officer has taken over"))
    row = mirror(session)
    client = Zoho()
    client.messages.extend([
        {"id": "2", "direction": "out", "content": "I am checking your account", "createdTime": "2026-09-19T10:01:00Z"},
        {"id": "3", "direction": "in", "content": "Thank you, any update?", "createdTime": "2026-09-19T10:02:00Z"},
    ])
    action = service.process_conversation_ticket(session, client, row)
    assert action.rule == "conversation_human_owned"


def test_whatsapp_clarification_uses_its_own_send_flag(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_WHATSAPP_AUTO_REPLY_EXECUTE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    row.channel = "WhatsApp"
    client = Zoho()
    action = service.process_conversation_ticket(session, client, row)
    assert action.action_type == "ask_clarification"
    assert action.executed
    assert "to" not in client.sent[0]


def test_changing_live_modes_does_not_reply_again_to_same_message(session, monkeypatch):
    monkeypatch.setenv("HELPDESK_DRAFT_EXECUTE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(graph_module.llm, "understand", lambda data: plan())
    row = mirror(session)
    client = Zoho()
    draft = service.process_conversation_ticket(session, client, row)
    enable_send(monkeypatch)
    result = service.process_conversation_ticket(session, client, row)
    assert result.id == draft.id
    assert len(client.sent) == 1


def test_reply_settings_change_cancels_prepared_send(session, monkeypatch):
    enable_send(monkeypatch)
    row = mirror(session)
    client = Zoho()
    def understand(data):
        monkeypatch.setenv("HELPDESK_EMAIL_AUTO_REPLY_EXECUTE", "false")
        get_settings.cache_clear()
        return plan()
    monkeypatch.setattr(graph_module.llm, "understand", understand)
    action = service.process_conversation_ticket(session, client, row)
    assert action.rule == "delivery_mode_changed"
    assert client.sent == []


def test_unread_attachment_filename_is_not_tool_evidence():
    from app.services.helpdesk_thread_context import ThreadContext, ThreadMessage
    context = ThreadContext("", "", False, True, [], ["1"], messages=[
        ThreadMessage("1", "in", "", "Candidate", "See attached", ["[Attachment: FOT.png; OCR not configured]"]),
    ])
    sources = service._evidence_messages(context)
    assert resolve_tools(sources, "1", "", CueRegistry(version="1"))["tool"] is None


def test_readable_ocr_can_identify_tool_without_filename():
    from app.services.helpdesk_thread_context import ThreadContext, ThreadMessage
    context = ThreadContext("", "", True, True, [], ["1"], messages=[
        ThreadMessage("1", "in", "", "Candidate", "See attached", ["[Attachment OCR: fot.png]\nTest Haven login error"]),
    ])
    sources = service._evidence_messages(context)
    assert resolve_tools(sources, "1", "", CueRegistry(version="1"))["tool"] == "Test Haven"


def test_conflicting_ocr_and_candidate_platform_requires_clarification():
    messages = [{"id": "1:ocr:0", "role": "candidate_attachment", "text": "Test Haven login"},
                {"id": "1", "role": "candidate", "text": "I am using FOT"}]
    result = resolve_tools(messages, "1", "", CueRegistry(version="1"))
    assert result["tool"] is None
    assert result["conflict"]
