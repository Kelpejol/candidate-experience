"""Build the Helpdesk AI's candidate-message context from Zoho threads.

The downstream classifier/draft writer should reason over what the candidate
actually said, not our latest outbound reply, an email quote trail, or a
bare "see screenshot" message with unread attachments. This module keeps
thread selection, HTML cleanup, quote stripping, short chat-burst context,
and optional attachment OCR in one testable place.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.integrations.azure_document_intelligence_client import (
    build_azure_document_intelligence_client,
)

INCOMING_DIRECTIONS = {"in", "incoming", "inbound"}
OUTGOING_DIRECTIONS = {"out", "outgoing", "outbound"}

MAX_CONTEXT_MESSAGES = 6
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 2500
MAX_ATTACHMENTS_TO_OCR = 3
MIN_USEFUL_OCR_ALNUM_CHARS = 8

OCR_EXTENSIONS = {
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".tif",
    ".tiff",
    ".xls",
    ".xlsx",
}

SENSITIVE_TEXT_PATTERNS = (
    (
        re.compile(r"\b(password|passcode|otp|pin|token|secret)\s*[:=]\s*\S+", re.I),
        lambda m: f"{m.group(1)}: [REDACTED]",
    ),
    (
        re.compile(r"\b(bvn|nin)\s*[:#=]?\s*\d{6,}\b", re.I),
        lambda m: f"{m.group(1)}: [REDACTED]",
    ),
    (
        re.compile(r"\b(account\s*(?:number|no\.?)|acct)\s*[:#=]?\s*\d{6,}\b", re.I),
        lambda m: f"{m.group(1)}: [REDACTED]",
    ),
    (
        re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
        lambda m: "[REDACTED_CARD_NUMBER]",
    ),
)


@dataclass
class ThreadMessage:
    id: str
    direction: str
    created_time: str
    author: str
    text: str
    attachment_notes: list[str]

    @property
    def candidate_visible_text(self) -> str:
        parts = [self.text, *self.attachment_notes]
        return "\n\n".join(part for part in parts if part.strip()).strip()

    @property
    def readable_candidate_content(self) -> str:
        parts = [
            self.text,
            *(
                note
                for note in self.attachment_notes
                if note.startswith("[Attachment OCR:")
            ),
        ]
        return "\n\n".join(part for part in parts if part.strip()).strip()


@dataclass
class ThreadContext:
    text: str
    latest_candidate_text: str
    has_readable_candidate_content: bool
    has_attachments: bool
    attachment_notes: list[str]
    selected_thread_ids: list[str]
    messages: list[ThreadMessage] = field(default_factory=list)
    latest_candidate_id: str = ""
    has_later_public_reply: bool = False


def normalize_thread_direction(value: str | None) -> str:
    key = re.sub(r"[^a-z]+", "", (value or "").lower())
    if key in INCOMING_DIRECTIONS:
        return "in"
    if key in OUTGOING_DIRECTIONS:
        return "out"
    return "unknown"


def parse_zoho_time(value: str | None) -> datetime:
    if not value:
        return datetime.min
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def clean_thread_text(raw: str | None) -> str:
    if not raw:
        return ""

    text = raw
    text = re.sub(r"(?is)<(blockquote|script|style)[^>]*>.*?</\1>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = strip_quoted_reply(text)
    text = "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    )
    return redact_sensitive_text(text.strip())


def redact_sensitive_text(text: str) -> str:
    """Redact obvious secrets before LLM classification/drafting.

    This is intentionally conservative: preserve useful ticket context while
    removing values candidates commonly paste into screenshots or emails.
    """
    redacted = text
    for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def strip_quoted_reply(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    kept: list[str] = []

    quote_markers = (
        re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.I),
        re.compile(r"^On .+ wrote:$", re.I),
        re.compile(r"^From:\s+.+", re.I),
        re.compile(r"^Sent:\s+.+", re.I),
        re.compile(r"^Subject:\s+.+", re.I),
        re.compile(r"^To:\s+.+", re.I),
    )

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if kept and any(pattern.match(stripped) for pattern in quote_markers):
            break
        kept.append(line)

    return "\n".join(kept).strip()


def is_candidate_thread(thread: dict[str, Any]) -> bool:
    if normalize_thread_direction(thread.get("direction")) != "in":
        return False
    if str(thread.get("status") or "").upper() == "DRAFT":
        return False
    if thread.get("isPrivate") is True or thread.get("isPublic") is False:
        return False
    return True


def is_public_message(thread: dict[str, Any]) -> bool:
    return (
        normalize_thread_direction(thread.get("direction")) in {"in", "out"}
        and str(thread.get("status") or "").upper() != "DRAFT"
        and str(thread.get("isPrivate", False)).lower() != "true"
        and str(thread.get("isPublic", True)).lower() != "false"
    )


def _thread_sort_key(thread: dict[str, Any]) -> tuple[datetime, str]:
    return (parse_zoho_time(thread.get("createdTime")), str(thread.get("id") or ""))


def _attachment_name(attachment: dict[str, Any]) -> str:
    return str(attachment.get("name") or attachment.get("fileName") or "attachment")


def _attachment_href(attachment: dict[str, Any]) -> str | None:
    href = attachment.get("href") or attachment.get("url") or attachment.get("downloadUrl")
    return str(href) if href else None


def _attachment_looks_ocr_supported(attachment: dict[str, Any]) -> bool:
    name = _attachment_name(attachment).lower()
    content_type = str(attachment.get("contentType") or attachment.get("mimeType") or "").lower()
    return (
        Path(name).suffix.lower() in OCR_EXTENSIONS
        or content_type.startswith("image/")
        or content_type in {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
    )


def _has_useful_ocr_text(text: str) -> bool:
    return len(re.findall(r"[A-Za-z0-9]", text)) >= MIN_USEFUL_OCR_ALNUM_CHARS


def extract_attachment_notes(zoho_client, attachments: list[dict[str, Any]]) -> list[str]:
    if not attachments:
        return []

    settings = get_settings()
    notes: list[str] = []
    ocr_attempts = 0
    ocr_client = None
    if settings.helpdesk_attachment_ocr_enabled:
        try:
            ocr_client = build_azure_document_intelligence_client(settings)
        except Exception:
            ocr_client = None

    for attachment in attachments:
        name = _attachment_name(attachment)
        href = _attachment_href(attachment)
        if not _attachment_looks_ocr_supported(attachment):
            notes.append(f"[Attachment: {name}] Unsupported file type for OCR; route to a human if this file is needed.")
            continue
        if ocr_attempts >= MAX_ATTACHMENTS_TO_OCR:
            notes.append(f"[Attachment: {name}] OCR skipped because this ticket has more than {MAX_ATTACHMENTS_TO_OCR} OCR-supported attachments; route to a human if this file is needed.")
            continue
        if not ocr_client:
            notes.append(f"[Attachment: {name}] OCR not configured; route to a human if this file is needed.")
            continue
        if not href:
            notes.append(f"[Attachment: {name}] No download URL was provided; route to a human if this file is needed.")
            continue

        ocr_attempts += 1
        try:
            data, content_type = zoho_client.download_attachment_content(href)
            if len(data) > MAX_ATTACHMENT_BYTES:
                notes.append(f"[Attachment: {name}] File is too large for OCR; route to a human if this file is needed.")
                continue
            extracted = redact_sensitive_text(
                ocr_client.read_bytes(data, content_type=content_type).strip()
            )
        except Exception:
            notes.append(f"[Attachment: {name}] OCR failed; route to a human if this file is needed.")
            continue

        if extracted and _has_useful_ocr_text(extracted):
            notes.append(f"[Attachment OCR: {name}]\n{extracted[:MAX_ATTACHMENT_TEXT_CHARS]}")
        else:
            notes.append(f"[Attachment: {name}] OCR found no useful readable text; route to a human if this file is needed.")

    return notes


def _message_from_thread(zoho_client, ticket_id: str, thread: dict[str, Any]) -> ThreadMessage | None:
    thread_id = str(thread["id"])
    detail = zoho_client.get_thread(ticket_id, thread_id)
    if not is_public_message({**thread, **detail}):
        return None
    raw_text = detail.get("content") or detail.get("summary") or thread.get("summary") or ""
    attachments = detail.get("attachments") or thread.get("attachments") or []
    direction = normalize_thread_direction(detail.get("direction") or thread.get("direction"))
    author = "Candidate" if direction == "in" else "Officer"
    return ThreadMessage(
        id=thread_id,
        direction=direction,
        created_time=str(detail.get("createdTime") or thread.get("createdTime") or ""),
        author=author,
        text=clean_thread_text(raw_text),
        attachment_notes=extract_attachment_notes(zoho_client, attachments),
    )


def _format_context(messages: list[ThreadMessage], latest_candidate: ThreadMessage) -> str:
    if not messages:
        return latest_candidate.candidate_visible_text

    lines = ["Recent Zoho ticket conversation:"]
    for message in messages:
        visible = message.candidate_visible_text
        if not visible:
            visible = "(no readable text)"
        lines.append(f"{message.author}: {visible}")
    lines.append("")
    lines.append("Latest candidate message to answer:")
    lines.append(latest_candidate.candidate_visible_text or "(no readable text)")
    return "\n\n".join(lines).strip()


def build_thread_context(
    zoho_client,
    ticket_id: str,
    *,
    conversational: bool = False,
    max_messages: int = MAX_CONTEXT_MESSAGES,
) -> ThreadContext:
    threads = zoho_client.list_ticket_threads(ticket_id).get("data", [])
    if not threads:
        return ThreadContext("", "", False, False, [], [])

    sorted_threads = sorted((t for t in threads if is_public_message(t)), key=_thread_sort_key)
    candidate_threads = [thread for thread in sorted_threads if is_candidate_thread(thread)]
    if not candidate_threads:
        return ThreadContext("", "", False, False, [], [])

    latest_candidate_thread = candidate_threads[-1]
    latest_candidate_id = str(latest_candidate_thread["id"])

    latest_index = sorted_threads.index(latest_candidate_thread)
    start = max(0, latest_index - max_messages + 1)
    selected_threads = sorted_threads[start : latest_index + 1]

    messages = [m for thread in selected_threads if (m := _message_from_thread(zoho_client, ticket_id, thread)) is not None]
    by_id = {message.id: message for message in messages}
    if latest_candidate_id not in by_id:
        return ThreadContext("", "", False, False, [], [])
    latest_candidate = by_id[latest_candidate_id]

    attachment_notes = [note for message in messages for note in message.attachment_notes]
    has_readable_candidate_content = bool(latest_candidate.readable_candidate_content.strip())
    return ThreadContext(
        text=_format_context(messages, latest_candidate),
        latest_candidate_text=latest_candidate.candidate_visible_text,
        has_readable_candidate_content=has_readable_candidate_content,
        has_attachments=bool(attachment_notes),
        attachment_notes=attachment_notes,
        selected_thread_ids=[message.id for message in messages],
        messages=messages,
        latest_candidate_id=latest_candidate_id,
        has_later_public_reply=any(t for t in sorted_threads[latest_index + 1:] if normalize_thread_direction(t.get("direction")) == "out"),
    )
