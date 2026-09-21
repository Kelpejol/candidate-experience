from scripts.analyze_helpdesk_history import (
    build_cue_candidates,
    build_proposals,
    collapse_ticket_histories,
    dedupe_raw_tickets,
    FatalExtractionError,
    hydrate_zoho_threads,
    hydrate_one_zoho_ticket,
    is_fatal_zoho_auth_error,
    message_from_thread,
    qa_pairs_from_ticket,
    redact_for_review,
    retry_call,
    safe_error_message,
    solution_text,
    summarize,
    TicketHistory,
    ticket_needs_retry,
)
import requests


def test_redacts_contact_and_secret_like_values():
    text = "Dear Jane Doe , Email me at jane@example.com or 08031234567. Student ID: CIPM/STD/B01166. password: hunter2"

    redacted = redact_for_review(text)

    assert "Jane Doe" not in redacted
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[STUDENT_ID]" in redacted
    assert "hunter2" not in redacted


def test_private_or_draft_threads_are_not_extracted():
    private = {
        "id": "1",
        "direction": "out",
        "isPrivate": True,
        "content": "internal note",
    }
    draft = {
        "id": "2",
        "direction": "out",
        "status": "DRAFT",
        "content": "draft reply",
    }

    assert message_from_thread(private) is None
    assert message_from_thread(draft) is None


def test_summary_thread_text_is_marked_as_lower_quality():
    message = message_from_thread(
        {
            "id": "1",
            "direction": "in",
            "createdTime": "2026-01-01T10:00:00+00:00",
            "summary": "I cannot login...",
        }
    )

    assert message is not None
    assert message.source_field == "summary"
    assert message.source_is_detail is False
    assert message.truncated_suspected is True


def test_reconstructs_candidate_burst_to_next_officer_answer():
    ticket = TicketHistory(
        ticket_id="t1",
        ticket_number="1001",
        subject="Cannot access my FOT test",
        channel="Email",
        status="Closed",
        created_time=None,
        messages=[
            message_from_thread({"id": "m1", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "content": "I cannot login"}),
            message_from_thread({"id": "m2", "direction": "in", "createdTime": "2026-01-01T10:01:00+00:00", "content": "It is for FOT"}),
            message_from_thread({"id": "m3", "direction": "out", "createdTime": "2026-01-01T10:02:00+00:00", "content": "Please use forgot password on the FOT portal."}),
        ],
    )
    ticket.messages = [message for message in ticket.messages if message]

    pairs = qa_pairs_from_ticket(ticket)

    assert len(pairs) == 1
    assert pairs[0].candidate_message_ids == ["m1", "m2"]
    assert pairs[0].officer_message_ids == ["m3"]
    assert pairs[0].tool_mentions == ["FOT"]
    assert "login_access" in pairs[0].issue_tags


def test_marks_waiting_replies_as_not_solutions():
    ticket = TicketHistory(
        ticket_id="t1",
        ticket_number=None,
        subject="Secure browser not downloading",
        channel="Email",
        status=None,
        created_time=None,
        messages=[
            message_from_thread({"id": "m1", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "content": "The SB is not downloading"}),
            message_from_thread({"id": "m2", "direction": "out", "createdTime": "2026-01-01T10:01:00+00:00", "content": "We will get back to you shortly."}),
        ],
    )
    ticket.messages = [message for message in ticket.messages if message]

    pair = qa_pairs_from_ticket(ticket)[0]
    proposals = build_proposals([pair], kb_chunks=[])

    assert "not_a_solution" in pair.risk_flags
    assert proposals == []


def test_solution_text_removes_footer_and_boilerplate():
    answer = """Dear [NAME],
Kindly be informed your saved answers are submitted automatically from the server.
How would you rate our customer service?
Good
Okay
Bad
Best Regards, [NAME].
3rd Katia Garden"""

    assert solution_text(answer) == "Dear [NAME],\nKindly be informed your saved answers are submitted automatically from the server."


def test_flags_security_sensitive_answers_for_review():
    ticket = TicketHistory(
        ticket_id="t1",
        ticket_number=None,
        subject="Secure browser",
        channel="Email",
        status=None,
        created_time=None,
        messages=[
            message_from_thread({"id": "m1", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "content": "Secure browser is not installing"}),
            message_from_thread({"id": "m2", "direction": "out", "createdTime": "2026-01-01T10:01:00+00:00", "content": "Uninstall your antivirus and install again."}),
        ],
    )
    ticket.messages = [message for message in ticket.messages if message]

    pair = qa_pairs_from_ticket(ticket)[0]
    proposals = build_proposals([pair], kb_chunks=[])

    assert "security_sensitive_instruction" in pair.risk_flags
    assert proposals[0]["recommended_action"] == "review_before_kb"


def test_cue_candidates_are_review_only_and_frequency_based():
    pairs = []
    for index in range(3):
        ticket = TicketHistory(
            ticket_id=f"t{index}",
            ticket_number=None,
            subject="CIPM assessment",
            channel="Email",
            status=None,
            created_time=None,
            messages=[
                message_from_thread({"id": f"m{index}a", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "content": "I cannot access CIPM test on FOT"}),
                message_from_thread({"id": f"m{index}b", "direction": "out", "createdTime": "2026-01-01T10:01:00+00:00", "content": "Use the FOT forgot password page."}),
            ],
        )
        ticket.messages = [message for message in ticket.messages if message]
        pairs.extend(qa_pairs_from_ticket(ticket))

    cues = build_cue_candidates(pairs)

    cipm = next(item for item in cues if item["phrase"] == "CIPM")
    assert cipm["status"] == "needs_officer_review"
    assert cipm["exclusive_candidate"] is True
    assert "approval is required" in cipm["warning"]


def test_summary_keeps_units_separate():
    ticket = TicketHistory(
        ticket_id="t1",
        ticket_number=None,
        subject="Login",
        channel="Email",
        status="Closed",
        created_time=None,
        messages=[
            message_from_thread({"id": "m1", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "content": "I cannot login"}),
        ],
    )
    ticket.messages = [message for message in ticket.messages if message]
    pairs = qa_pairs_from_ticket(ticket)

    summary = summarize([ticket], pairs, proposals=[])

    assert summary["ticket_count"] == 1
    assert summary["ticket_extraction_error_count"] == 0
    assert summary["candidate_message_count"] == 1
    assert summary["qa_pair_count"] == 1
    assert summary["unanswered_pair_count"] == 1


def test_safe_error_message_redacts_zoho_credentials():
    error = Exception("url /oauth/v2/token?refresh_token=abc&client_id=id1&client_secret=secret")

    message = safe_error_message(error)

    assert "abc" not in message
    assert "id1" not in message
    assert "client_secret=secret" not in message
    assert "refresh_token=[REDACTED]" in message


def test_retry_call_recovers_from_transient_failure(monkeypatch):
    monkeypatch.setattr("scripts.analyze_helpdesk_history.time.sleep", lambda _: None)
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("slow")
        return "ok"

    assert retry_call("flaky", retries=2, delay_seconds=0, func=flaky) == "ok"
    assert calls["count"] == 2


def test_retry_call_aborts_on_zoho_token_http_failure(monkeypatch):
    monkeypatch.setattr("scripts.analyze_helpdesk_history.time.sleep", lambda _: None)
    response = requests.Response()
    response.status_code = 400
    response.url = "https://accounts.zoho.com/oauth/v2/token?refresh_token=secret"
    error = requests.HTTPError("400 Client Error")
    error.response = response
    calls = {"count": 0}

    def bad_auth():
        calls["count"] += 1
        raise error

    assert is_fatal_zoho_auth_error(error) is True
    try:
        retry_call("auth", retries=3, delay_seconds=0, func=bad_auth)
    except FatalExtractionError as exc:
        assert "refresh_token=[REDACTED]" in str(exc)
    else:
        raise AssertionError("FatalExtractionError was not raised")
    assert calls["count"] == 1


def test_retry_call_aborts_on_zoho_token_failure_when_url_is_only_in_message(monkeypatch):
    monkeypatch.setattr("scripts.analyze_helpdesk_history.time.sleep", lambda _: None)
    error = requests.HTTPError(
        "400 Client Error:  for url: "
        "https://accounts.zoho.com/oauth/v2/token?refresh_token=secret&client_id=id"
    )
    calls = {"count": 0}

    def bad_auth():
        calls["count"] += 1
        raise error

    assert is_fatal_zoho_auth_error(error) is True
    try:
        retry_call("auth", retries=3, delay_seconds=0, func=bad_auth)
    except FatalExtractionError as exc:
        assert "refresh_token=[REDACTED]" in str(exc)
    else:
        raise AssertionError("FatalExtractionError was not raised")
    assert calls["count"] == 1


def test_retry_call_aborts_on_zoho_desk_unauthorized(monkeypatch):
    monkeypatch.setattr("scripts.analyze_helpdesk_history.time.sleep", lambda _: None)
    error = requests.HTTPError(
        "401 Client Error:  for url: "
        "https://desk.zoho.com/api/v1/tickets/t1/threads?from=0&limit=100"
    )
    calls = {"count": 0}

    def bad_auth():
        calls["count"] += 1
        raise error

    assert is_fatal_zoho_auth_error(error) is True
    try:
        retry_call("threads", retries=3, delay_seconds=0, func=bad_auth)
    except FatalExtractionError:
        pass
    else:
        raise AssertionError("FatalExtractionError was not raised")
    assert calls["count"] == 1


def test_hydrate_ticket_does_not_convert_fatal_auth_to_ticket_error(monkeypatch):
    class BadClient:
        def list_ticket_threads(self, ticket_id):
            raise FatalExtractionError("auth failed")

    ticket = {
        "id": "t1",
        "ticketNumber": "1001",
        "subject": "Login",
        "channel": "Email",
        "status": "Open",
        "createdTime": "2026-01-01T10:00:00Z",
    }

    try:
        hydrate_one_zoho_ticket(ticket, retries=0, retry_delay=0, thread_detail_mode="always", client=BadClient())
    except FatalExtractionError:
        pass
    else:
        raise AssertionError("FatalExtractionError was converted into a ticket extraction error")


def test_parallel_hydration_does_not_convert_fatal_auth_to_worker_error(monkeypatch, tmp_path):
    def bad_hydrate(*args, **kwargs):
        raise FatalExtractionError("auth failed")

    monkeypatch.setattr("scripts.analyze_helpdesk_history.build_zoho_desk_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("scripts.analyze_helpdesk_history.hydrate_one_zoho_ticket", bad_hydrate)

    ticket = {
        "id": "t1",
        "ticketNumber": "1001",
        "subject": "Login",
        "channel": "Email",
        "status": "Open",
        "createdTime": "2026-01-01T10:00:00Z",
    }

    try:
        hydrate_zoho_threads(
            [ticket],
            retries=0,
            retry_delay=0,
            thread_detail_mode="always",
            stream_path=tmp_path / "tickets.jsonl",
            workers=2,
        )
    except FatalExtractionError:
        pass
    else:
        raise AssertionError("FatalExtractionError was converted into a worker_failed row")


def test_resume_quality_flags_failed_and_summary_fallback_tickets_for_retry():
    failed = TicketHistory(
        ticket_id="t1",
        ticket_number=None,
        subject="Login",
        channel="Email",
        status=None,
        created_time=None,
        extraction_errors=["thread_list_failed: timeout"],
    )
    fallback = TicketHistory(
        ticket_id="t2",
        ticket_number=None,
        subject="Login",
        channel="Email",
        status=None,
        created_time=None,
        messages=[
            message_from_thread({"id": "m1", "direction": "in", "createdTime": "2026-01-01T10:00:00+00:00", "summary": "Cannot login..."})
        ],
    )
    clean = TicketHistory(
        ticket_id="t3",
        ticket_number=None,
        subject="Login",
        channel="Email",
        status=None,
        created_time=None,
        messages=[
            message_from_thread(
                {
                    "id": "m2",
                    "direction": "in",
                    "createdTime": "2026-01-01T10:00:00+00:00",
                    "content": "Cannot login",
                    "__source_is_detail": True,
                }
            )
        ],
    )

    assert ticket_needs_retry(failed, require_full_detail=True) is True
    assert ticket_needs_retry(fallback, require_full_detail=True) is True
    assert ticket_needs_retry(clean, require_full_detail=True) is False


def test_collapse_ticket_histories_keeps_best_duplicate_version():
    failed = TicketHistory(
        ticket_id="t1",
        ticket_number="1001",
        subject="Login",
        channel="Email",
        status=None,
        created_time=None,
        extraction_errors=["thread_list_failed: timeout"],
    )
    clean = TicketHistory(
        ticket_id="t1",
        ticket_number="1001",
        subject="Login",
        channel="Email",
        status=None,
        created_time=None,
        messages=[
            message_from_thread(
                {
                    "id": "m1",
                    "direction": "in",
                    "createdTime": "2026-01-01T10:00:00+00:00",
                    "content": "Cannot login",
                    "__source_is_detail": True,
                }
            )
        ],
    )

    collapsed = collapse_ticket_histories([failed, clean], require_full_detail=True)

    assert collapsed == [clean]


def test_dedupe_raw_tickets_keeps_first_ticket_id():
    tickets = [
        {"id": "t1", "subject": "first"},
        {"id": "t1", "subject": "duplicate"},
        {"id": "t2", "subject": "second"},
    ]

    assert dedupe_raw_tickets(tickets) == [tickets[0], tickets[2]]
