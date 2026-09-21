from app.core.config import get_settings
from app.services import helpdesk_thread_context
from app.services.helpdesk_thread_context import (
    build_thread_context,
    clean_thread_text,
    normalize_thread_direction,
)


class _FakeZohoClient:
    def __init__(self, threads, details=None, downloads=None):
        self.threads = threads
        self.details = details or {}
        self.downloads = downloads or {}

    def list_ticket_threads(self, ticket_id):
        return {"data": self.threads}

    def get_thread(self, ticket_id, thread_id):
        return self.details.get(thread_id, next(t for t in self.threads if str(t["id"]) == thread_id))

    def download_attachment_content(self, href):
        return self.downloads[href]


def _thread(thread_id, direction="in", created="2026-09-18T10:00:00Z", **extra):
    data = {
        "id": thread_id,
        "direction": direction,
        "createdTime": created,
        "summary": "",
    }
    data.update(extra)
    return data


def test_direction_variants_are_normalized():
    assert normalize_thread_direction("IN") == "in"
    assert normalize_thread_direction("incoming") == "in"
    assert normalize_thread_direction("Inbound") == "in"
    assert normalize_thread_direction("OUT") == "out"
    assert normalize_thread_direction("outbound") == "out"
    assert normalize_thread_direction("weird") == "unknown"


def test_html_entities_and_quoted_email_are_removed():
    text = clean_thread_text(
        "<p>I can&apos;t login&nbsp;&amp; need help.</p>"
        "<blockquote>old quoted content</blockquote>"
        "On Tue, Support wrote:\nPlease try again"
    )

    assert text == "I can't login & need help."


def test_obvious_secrets_are_redacted_from_message_text():
    text = clean_thread_text(
        "Password: hunter2\nOTP=123456\nBVN 22123456789\nCard 1234 5678 9012 3456"
    )

    assert "hunter2" not in text
    assert "123456" not in text
    assert "22123456789" not in text
    assert "1234 5678 9012 3456" not in text
    assert "Password: [REDACTED]" in text
    assert "OTP: [REDACTED]" in text
    assert "BVN: [REDACTED]" in text
    assert "[REDACTED_CARD_NUMBER]" in text


def test_email_context_uses_recent_conversation_and_latest_candidate_message():
    threads = [
        _thread("1", direction="in", created="2026-09-18T10:00:00Z", summary="First"),
        _thread("2", direction="out", created="2026-09-18T10:01:00Z", summary="Officer asks"),
        _thread("3", direction="incoming", created="2026-09-18T10:02:00Z", summary="Second"),
    ]
    details = {
        "1": {**threads[0], "content": "<p>I cannot login</p>"},
        "2": {**threads[1], "content": "<p>Which assessment?</p>"},
        "3": {**threads[2], "content": "<p>It is the FOT test</p>"},
    }

    context = build_thread_context(_FakeZohoClient(threads, details), "ticket-1")

    assert "Candidate: I cannot login" in context.text
    assert "Officer: Which assessment?" in context.text
    assert "Latest candidate message to answer:\n\nIt is the FOT test" in context.text
    assert context.latest_candidate_text == "It is the FOT test"
    assert context.selected_thread_ids == ["1", "2", "3"]


def test_conversational_context_includes_recent_back_and_forth():
    threads = [
        _thread("1", created="2026-09-18T10:00:00Z", summary="hello"),
        _thread("2", created="2026-09-18T10:00:10Z", summary="I cannot login"),
        _thread("3", created="2026-09-18T10:00:20Z", summary="see screenshot"),
        _thread("4", direction="out", created="2026-09-18T10:00:30Z", summary="Which test?"),
        _thread("5", created="2026-09-18T10:00:40Z", summary="FOT"),
    ]
    details = {
        "1": {**threads[0], "content": "hello"},
        "2": {**threads[1], "content": "I cannot login"},
        "3": {**threads[2], "content": "see screenshot"},
        "4": {**threads[3], "content": "Which test?"},
        "5": {**threads[4], "content": "FOT"},
    }

    context = build_thread_context(
        _FakeZohoClient(threads, details),
        "ticket-1",
        conversational=True,
    )

    assert context.selected_thread_ids == ["1", "2", "3", "4", "5"]
    assert "Officer: Which test?" in context.text
    assert "Candidate: hello" in context.text
    assert "Candidate: I cannot login" in context.text
    assert context.latest_candidate_text == "FOT"


def test_attachment_without_ocr_is_not_counted_as_readable_content(monkeypatch):
    monkeypatch.setenv("HELPDESK_ATTACHMENT_OCR_ENABLED", "false")
    get_settings.cache_clear()

    threads = [
        _thread(
            "1",
            summary="",
            attachments=[{"name": "screenshot.png", "href": "/tickets/t/attachments/a/content"}],
        )
    ]
    details = {"1": {**threads[0], "content": "", "attachments": threads[0]["attachments"]}}

    context = build_thread_context(_FakeZohoClient(threads, details), "ticket-1")

    assert context.has_attachments is True
    assert context.has_readable_candidate_content is False
    assert "OCR not configured" in context.text

    get_settings.cache_clear()


def test_attachment_ocr_text_is_included_when_enabled(monkeypatch):
    monkeypatch.setenv("HELPDESK_ATTACHMENT_OCR_ENABLED", "true")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "key")
    get_settings.cache_clear()

    class _FakeOcrClient:
        def read_bytes(self, data, content_type="application/octet-stream"):
            assert data == b"image-bytes"
            assert content_type == "image/png"
            return "Error: camera permission denied"

    monkeypatch.setattr(
        helpdesk_thread_context,
        "build_azure_document_intelligence_client",
        lambda settings: _FakeOcrClient(),
    )

    threads = [
        _thread(
            "1",
            summary="see attached",
            attachments=[{"name": "screenshot.png", "href": "/tickets/t/attachments/a/content"}],
        )
    ]
    details = {"1": {**threads[0], "content": "see attached", "attachments": threads[0]["attachments"]}}
    downloads = {"/tickets/t/attachments/a/content": (b"image-bytes", "image/png")}

    context = build_thread_context(_FakeZohoClient(threads, details, downloads), "ticket-1")

    assert context.has_readable_candidate_content is True
    assert "[Attachment OCR: screenshot.png]" in context.text
    assert "camera permission denied" in context.text

    get_settings.cache_clear()


def test_ocr_text_is_redacted_before_context(monkeypatch):
    monkeypatch.setenv("HELPDESK_ATTACHMENT_OCR_ENABLED", "true")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "key")
    get_settings.cache_clear()

    class _FakeOcrClient:
        def read_bytes(self, data, content_type="application/octet-stream"):
            return "Password: hunter2\nOTP: 123456\nCamera denied"

    monkeypatch.setattr(
        helpdesk_thread_context,
        "build_azure_document_intelligence_client",
        lambda settings: _FakeOcrClient(),
    )

    threads = [
        _thread(
            "1",
            summary="see attached",
            attachments=[{"name": "screenshot.png", "href": "/attachments/a/content"}],
        )
    ]
    details = {"1": {**threads[0], "content": "see attached", "attachments": threads[0]["attachments"]}}
    downloads = {"/attachments/a/content": (b"image-bytes", "image/png")}

    context = build_thread_context(_FakeZohoClient(threads, details, downloads), "ticket-1")

    assert "hunter2" not in context.text
    assert "123456" not in context.text
    assert "Password: [REDACTED]" in context.text
    assert "OTP: [REDACTED]" in context.text

    get_settings.cache_clear()


def test_low_value_ocr_text_is_not_counted_as_readable(monkeypatch):
    monkeypatch.setenv("HELPDESK_ATTACHMENT_OCR_ENABLED", "true")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "key")
    get_settings.cache_clear()

    class _FakeOcrClient:
        def read_bytes(self, data, content_type="application/octet-stream"):
            return "!!!"

    monkeypatch.setattr(
        helpdesk_thread_context,
        "build_azure_document_intelligence_client",
        lambda settings: _FakeOcrClient(),
    )

    threads = [
        _thread(
            "1",
            summary="",
            attachments=[{"name": "blank.png", "href": "/attachments/a/content"}],
        )
    ]
    details = {"1": {**threads[0], "content": "", "attachments": threads[0]["attachments"]}}
    downloads = {"/attachments/a/content": (b"image-bytes", "image/png")}

    context = build_thread_context(_FakeZohoClient(threads, details, downloads), "ticket-1")

    assert context.has_readable_candidate_content is False
    assert "OCR found no useful readable text" in context.text

    get_settings.cache_clear()


def test_only_first_three_supported_attachments_are_ocred(monkeypatch):
    monkeypatch.setenv("HELPDESK_ATTACHMENT_OCR_ENABLED", "true")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "key")
    get_settings.cache_clear()

    class _FakeOcrClient:
        def __init__(self):
            self.calls = 0

        def read_bytes(self, data, content_type="application/octet-stream"):
            self.calls += 1
            return f"Readable text {self.calls}"

    ocr_client = _FakeOcrClient()
    monkeypatch.setattr(
        helpdesk_thread_context,
        "build_azure_document_intelligence_client",
        lambda settings: ocr_client,
    )

    attachments = [
        {"name": f"screenshot-{i}.png", "href": f"/attachments/{i}/content"}
        for i in range(5)
    ]
    threads = [_thread("1", summary="see attached", attachments=attachments)]
    details = {"1": {**threads[0], "content": "see attached", "attachments": attachments}}
    downloads = {
        f"/attachments/{i}/content": (b"image-bytes", "image/png")
        for i in range(5)
    }

    context = build_thread_context(_FakeZohoClient(threads, details, downloads), "ticket-1")

    assert ocr_client.calls == 3
    assert "Readable text 3" in context.text
    assert "more than 3 OCR-supported attachments" in context.text

    get_settings.cache_clear()


def test_no_candidate_threads_is_unreadable():
    threads = [_thread("1", direction="out", summary="Our own reply")]

    context = build_thread_context(_FakeZohoClient(threads), "ticket-1")

    assert context.text == ""
    assert context.has_readable_candidate_content is False


def test_private_draft_and_unknown_messages_are_excluded_from_context():
    threads = [
        _thread("1", summary="FOT login problem"),
        _thread("2", direction="out", summary="PRIVATE SECRET", isPrivate=True),
        _thread("3", direction="out", summary="UNSENT DRAFT", status="DRAFT"),
        _thread("4", direction="unknown", summary="UNKNOWN NOTE"),
        _thread("5", created="2026-09-18T10:01:00Z", summary="Still failing"),
    ]
    result = build_thread_context(_FakeZohoClient(threads), "ticket")
    assert result.selected_thread_ids == ["1", "5"]
    assert "SECRET" not in result.text
    assert "DRAFT" not in result.text
    assert "NOTE" not in result.text


def test_detail_privacy_overrides_public_summary():
    threads = [_thread("1", direction="out", summary="public summary"),
               _thread("2", created="2026-09-18T10:01:00Z", summary="FOT")]
    details = {"1": {**threads[0], "isPublic": False, "content": "PRIVATE NOTE"}}
    result = build_thread_context(_FakeZohoClient(threads, details), "ticket")
    assert result.selected_thread_ids == ["2"]
    assert "PRIVATE" not in result.text


def test_context_orders_different_timezone_offsets_by_actual_time():
    threads = [_thread("1", created="2026-09-18T11:00:00+02:00", summary="Earlier"),
               _thread("2", created="2026-09-18T10:00:00Z", summary="Later")]
    result = build_thread_context(_FakeZohoClient(threads), "ticket")
    assert result.latest_candidate_id == "2"
