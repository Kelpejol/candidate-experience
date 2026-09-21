"""Extract conservative knowledge/cue proposals from historical Zoho helpdesk.

This is an offline analysis tool. It never writes to Zoho, never updates the KB,
and never treats frequency as approval. The output is meant for officer review:
evidence-rich Q&A pairs, deduplicated proposal candidates, conflicts, and a
summary of what the data actually contained.

Examples:
  PYTHONPATH=. .venv/bin/python scripts/analyze_helpdesk_history.py --source local
  PYTHONPATH=. .venv/bin/python scripts/analyze_helpdesk_history.py --source zoho --max-tickets 3000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.database import engine
from app.core.vocabulary import ALLOWED_TOOLS
from app.integrations.zoho_auth import ZohoAuthError
from app.integrations.zoho_desk_client import build_zoho_desk_client
from app.models.helpdesk_ticket_mirror import HelpdeskTicketMirror
from app.services.helpdesk_kb_service import GENERAL_SCOPE, load_kb_chunks
from app.services.helpdesk_thread_context import (
    clean_thread_text,
    is_public_message,
    normalize_thread_direction,
    parse_zoho_time,
)


PII_PATTERNS = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[EMAIL]"),
    (re.compile(r"\b(?:\+?234|0)?[789]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{2,8}/[A-Z]{2,8}/[A-Z0-9/-]{3,}\b", re.I), "[STUDENT_ID]"),
    (re.compile(r"\b(?:student\s*(?:id|number)|username)\s*[:#=]?\s*[A-Z0-9/-]{4,}\b", re.I), "[STUDENT_ID]"),
    (re.compile(r"\b\d{10,}\b"), "[NUMBER]"),
)

ISSUE_KEYWORDS = {
    "login_access": ("login", "log in", "access", "password", "credential", "username", "portal"),
    "secure_browser": ("secure browser", "safe exam browser", " sb ", "download", "install"),
    "test_start": ("start test", "begin test", "launch", "assessment", "exam", "test link"),
    "submission": ("submit", "submission", "submitted", "upload", "finish"),
    "reschedule": ("reschedule", "rescheduled", "missed", "another date", "new date"),
    "result": ("result", "score", "shortlist", "feedback"),
    "verification": ("id card", "passport", "face", "camera", "webcam", "microphone"),
    "payment": ("payment", "paid", "invoice", "receipt"),
}

STOPWORDS = {
    "about",
    "above",
    "again",
    "also",
    "because",
    "below",
    "candidate",
    "could",
    "dear",
    "dragnet",
    "email",
    "hello",
    "issue",
    "kindly",
    "please",
    "regards",
    "support",
    "thanks",
    "there",
    "ticket",
    "would",
    "your",
}

GENERIC_GREETING_NAMES = {
    "candidate",
    "cipm support team",
    "dragnet",
    "dragnet support",
    "ma",
    "sir",
    "support",
    "team",
}


@dataclass
class HistoryMessage:
    id: str
    role: str
    created_time: str
    text: str
    attachment_count: int = 0
    source_field: str = ""
    source_is_detail: bool = False
    truncated_suspected: bool = False


@dataclass
class TicketHistory:
    ticket_id: str
    ticket_number: str | None
    subject: str
    channel: str
    status: str | None
    created_time: str | None
    messages: list[HistoryMessage] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)


@dataclass
class QAPair:
    id: str
    ticket_id: str
    ticket_number: str | None
    channel: str
    subject: str
    candidate_message_ids: list[str]
    officer_message_ids: list[str]
    candidate_text: str
    officer_answer: str
    issue_tags: list[str]
    tool_mentions: list[str]
    attachment_count: int
    outcome: str
    risk_flags: list[str]
    quality_flags: list[str] = field(default_factory=list)


class FatalExtractionError(RuntimeError):
    """Raised when the run should stop instead of writing many bad rows."""


def redact_for_review(text: str) -> str:
    redacted = clean_thread_text(text)
    for pattern, replacement in PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = redact_greeting_names(redacted)
    return redacted.strip()


def redact_greeting_names(text: str) -> str:
    def replace_dear(match: re.Match[str]) -> str:
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        if name.lower() in GENERIC_GREETING_NAMES:
            return match.group(0)
        return "Dear [NAME]"

    text = re.sub(
        r"\bDear\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})(?=\s*,)",
        replace_dear,
        text,
    )
    text = re.sub(
        r"\bMy name is\s+[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4}",
        "My name is [NAME]",
        text,
    )
    text = re.sub(
        r"\b(Regards|Thanks|Thank you),?\s+[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}\.?\b",
        r"\1, [NAME]",
        text,
    )
    return text


def text_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _first_text_entry(thread: dict[str, Any]) -> tuple[str, str]:
    for key in ("content", "plainText", "contentText", "summary"):
        value = thread.get(key)
        if isinstance(value, str) and value.strip():
            return value, key
    return "", ""


def _first_text_field(thread: dict[str, Any]) -> str:
    return _first_text_entry(thread)[0]


def _looks_truncated(text: str, source_field: str, source_is_detail: bool) -> bool:
    stripped = text.strip()
    if source_field == "summary" and not source_is_detail:
        return True
    return bool(re.search(r"(?:\.\.\.|…)\s*$", stripped))


def _attachment_count(thread: dict[str, Any]) -> int:
    attachments = thread.get("attachments") or thread.get("attachment") or []
    return len(attachments) if isinstance(attachments, list) else 0


def message_from_thread(thread: dict[str, Any]) -> HistoryMessage | None:
    if not is_public_message(thread):
        return None
    direction = normalize_thread_direction(thread.get("direction"))
    if direction not in {"in", "out"}:
        return None
    raw_text, source_field = _first_text_entry(thread)
    source_is_detail = bool(thread.get("__source_is_detail"))
    text = redact_for_review(raw_text)
    attachment_count = _attachment_count(thread)
    if not text and not attachment_count:
        return None
    role = "candidate" if direction == "in" else "officer"
    if attachment_count and not text:
        text = "[Attachment present; content was not available in this export.]"
    return HistoryMessage(
        id=str(thread.get("id") or ""),
        role=role,
        created_time=str(thread.get("createdTime") or ""),
        text=text,
        attachment_count=attachment_count,
        source_field=source_field,
        source_is_detail=source_is_detail,
        truncated_suspected=_looks_truncated(text, source_field, source_is_detail),
    )


def sort_messages(messages: Iterable[HistoryMessage]) -> list[HistoryMessage]:
    return sorted(messages, key=lambda m: (parse_zoho_time(m.created_time), m.id))


def unique_texts(messages: list[HistoryMessage]) -> list[str]:
    seen: set[str] = set()
    texts: list[str] = []
    for message in messages:
        if not message.text:
            continue
        fingerprint = text_fingerprint(message.text)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        texts.append(message.text)
    return texts


def collapse_role_groups(messages: list[HistoryMessage]) -> list[list[HistoryMessage]]:
    groups: list[list[HistoryMessage]] = []
    for message in sort_messages(messages):
        if not groups or groups[-1][0].role != message.role:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def issue_tags(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    tags = [
        name
        for name, keywords in ISSUE_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return tags or ["other"]


def tool_mentions(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    found = []
    for tool in sorted(ALLOWED_TOOLS):
        phrase = re.sub(r"[^a-z0-9]+", " ", tool.lower()).strip()
        if f" {phrase} " in f" {normalized} ":
            found.append(tool)
    return found


def risk_flags(candidate: str, answer: str, tools: list[str]) -> list[str]:
    flags = []
    answer_l = answer.lower()
    candidate_l = candidate.lower()
    if len(tools) > 1:
        flags.append("multiple_tools_mentioned")
    if "secure browser" in candidate_l or re.search(r"\bsb\b", candidate_l):
        flags.append("secure_browser_needs_tool_or_stage")
    if re.search(r"\b(uninstall|disable)\b.*\b(antivirus|firewall|security)\b", answer_l):
        flags.append("security_sensitive_instruction")
    if re.search(r"\bguarantee|valid credentials|successfully submitted\b", answer_l):
        flags.append("asserts_backend_state")
    if re.search(r"\bshare|send\b.*\b(password|otp|pin)\b", answer_l):
        flags.append("asks_for_secret")
    if re.search(r"\bwait|hold on|we will get back|forwarded|customer service team|please see this\b", answer_l):
        flags.append("not_a_solution")
    return flags


def strip_email_footer(text: str) -> str:
    markers = (
        r"how would you rate our customer service\?",
        r"best regards,",
        r"3rd katia garden",
        r"www\.dragnet-solutions\.com",
    )
    cut = len(text)
    lowered = text.lower()
    for marker in markers:
        match = re.search(marker, lowered)
        if match:
            cut = min(cut, match.start())
    return text[:cut].strip()


def solution_text(answer: str) -> str:
    answer = strip_email_footer(answer)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", answer) if part.strip()]
    useful = []
    boilerplate_patterns = (
        r"thank you for reaching out",
        r"it has been ticketed with the id",
        r"customer service team",
        r"for reference",
    )
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        if any(re.search(pattern, lowered) for pattern in boilerplate_patterns):
            continue
        useful.append(paragraph)
    return "\n\n".join(useful).strip()


def qa_pairs_from_ticket(ticket: TicketHistory) -> list[QAPair]:
    pairs: list[QAPair] = []
    groups = collapse_role_groups(ticket.messages)
    for index, group in enumerate(groups):
        if group[0].role != "candidate":
            continue
        next_group = groups[index + 1] if index + 1 < len(groups) else []
        candidate_text = "\n\n".join(unique_texts(group)).strip()
        officer_text = (
            "\n\n".join(unique_texts([m for m in next_group if m.role == "officer"])).strip()
            if next_group and next_group[0].role == "officer"
            else ""
        )
        pair_id = text_fingerprint(f"{ticket.ticket_id}:{','.join(m.id for m in group)}")
        tools = sorted(set(tool_mentions(f"{ticket.subject}\n{candidate_text}\n{officer_text}")))
        quality_flags = sorted({
            flag
            for message in [*group, *(next_group if next_group and next_group[0].role == "officer" else [])]
            for flag in (
                ["message_from_summary"] if not message.source_is_detail else []
            ) + (
                ["truncated_suspected"] if message.truncated_suspected else []
            )
        })
        pairs.append(
            QAPair(
                id=pair_id,
                ticket_id=ticket.ticket_id,
                ticket_number=ticket.ticket_number,
                channel=ticket.channel,
                subject=ticket.subject,
                candidate_message_ids=[m.id for m in group],
                officer_message_ids=[m.id for m in next_group] if next_group and next_group[0].role == "officer" else [],
                candidate_text=candidate_text,
                officer_answer=solution_text(officer_text) or officer_text,
                issue_tags=issue_tags(f"{ticket.subject}\n{candidate_text}"),
                tool_mentions=tools,
                attachment_count=sum(m.attachment_count for m in group),
                outcome="answered" if officer_text else "unanswered",
                risk_flags=risk_flags(candidate_text, officer_text, tools),
                quality_flags=quality_flags,
            )
        )
    return pairs


def extract_candidate_terms(pairs: list[QAPair]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for pair in pairs:
        words = re.findall(r"[a-z][a-z0-9]{2,}", pair.candidate_text.lower())
        for word in words:
            if word not in STOPWORDS:
                counts[word] += 1
        for phrase in ("secure browser", "test haven", "forgot password", "id card", "start test"):
            if phrase in pair.candidate_text.lower():
                counts[phrase] += 3
    return counts


def answer_signature(answer: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", answer.strip())
    useful = " ".join(s for s in sentences if len(s.split()) >= 4)
    return text_fingerprint(useful[:800] or answer)


def load_current_kb_texts(kb_dir: str) -> list[dict[str, str]]:
    try:
        return load_kb_chunks(kb_dir)
    except Exception:
        return []


def lexical_overlap(left: str, right: str) -> float:
    l_words = {w for w in re.findall(r"[a-z0-9]{3,}", left.lower()) if w not in STOPWORDS}
    r_words = {w for w in re.findall(r"[a-z0-9]{3,}", right.lower()) if w not in STOPWORDS}
    if not l_words or not r_words:
        return 0.0
    return len(l_words & r_words) / len(l_words | r_words)


def best_kb_match(pair: QAPair, kb_chunks: list[dict[str, str]]) -> dict[str, Any] | None:
    best: tuple[float, dict[str, str]] | None = None
    query = f"{pair.subject}\n{pair.candidate_text}\n{pair.officer_answer}"
    for chunk in kb_chunks:
        score = lexical_overlap(query, chunk["text"])
        if best is None or score > best[0]:
            best = (score, chunk)
    if best is None:
        return None
    score, chunk = best
    return {
        "score": round(score, 4),
        "source": chunk.get("source"),
        "heading": chunk.get("heading"),
        "scope": chunk.get("scope", GENERAL_SCOPE),
    }


def build_proposals(pairs: list[QAPair], kb_chunks: list[dict[str, str]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[QAPair]] = defaultdict(list)
    for pair in pairs:
        if pair.outcome != "answered":
            continue
        answer = solution_text(pair.officer_answer) or pair.officer_answer
        if "not_a_solution" in pair.risk_flags or not answer.strip():
            continue
        key = (pair.issue_tags[0], answer_signature(pair.officer_answer))
        buckets[key].append(pair)

    proposals = []
    for (primary_issue, signature), bucket in sorted(buckets.items(), key=lambda item: len(item[1]), reverse=True):
        representative = bucket[0]
        all_tools = sorted({tool for pair in bucket for tool in pair.tool_mentions})
        conflicts = sorted({flag for pair in bucket for flag in pair.risk_flags})
        kb_match = best_kb_match(representative, kb_chunks)
        needs_review = bool(conflicts) or not kb_match or kb_match["score"] < 0.18
        proposals.append(
            {
                "type": "knowledge_update",
                "status": "needs_officer_review",
                "issue": primary_issue,
                "frequency": len(bucket),
                "candidate_examples": [
                    {
                        "ticket_id": pair.ticket_id,
                        "ticket_number": pair.ticket_number,
                        "message_ids": pair.candidate_message_ids,
                        "text": pair.candidate_text[:1200],
                    }
                    for pair in bucket[:5]
                ],
                "proposed_answer_from_history": (solution_text(representative.officer_answer) or representative.officer_answer)[:1800],
                "tool_mentions": all_tools,
                "risk_flags": conflicts,
                "current_kb_best_match": kb_match,
                "recommended_action": "review_before_kb" if needs_review else "compare_and_optionally_merge",
                "signature": signature,
            }
        )
    return proposals


def build_cue_candidates(pairs: list[QAPair]) -> list[dict[str, Any]]:
    cue_counts: dict[str, Counter[str]] = defaultdict(Counter)
    evidence: dict[str, list[QAPair]] = defaultdict(list)
    patterns = (
        r"\b(?:for|with|on|using|taking)\s+([A-Z][A-Za-z0-9&./ -]{2,40})",
        r"\b([A-Z]{2,8}(?:\s+[A-Z]{2,8})?)\b",
    )
    for pair in pairs:
        if not pair.tool_mentions:
            continue
        text = f"{pair.subject}\n{pair.candidate_text}"
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                phrase = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
                if len(phrase) < 3 or phrase.lower() in STOPWORDS:
                    continue
                if phrase in ALLOWED_TOOLS:
                    continue
                for tool in pair.tool_mentions:
                    cue_counts[phrase][tool] += 1
                if len(evidence[phrase]) < 5:
                    evidence[phrase].append(pair)

    proposals = []
    for phrase, counts in sorted(cue_counts.items(), key=lambda item: sum(item[1].values()), reverse=True):
        total = sum(counts.values())
        if total < 2:
            continue
        tools = sorted(counts)
        proposals.append(
            {
                "type": "routing_cue",
                "status": "needs_officer_review",
                "phrase": phrase,
                "observed_tools": dict(counts),
                "exclusive_candidate": len(tools) == 1 and total >= 3,
                "frequency": total,
                "evidence": [
                    {
                        "ticket_id": pair.ticket_id,
                        "ticket_number": pair.ticket_number,
                        "subject": pair.subject,
                        "candidate_text": pair.candidate_text[:700],
                        "tool_mentions": pair.tool_mentions,
                    }
                    for pair in evidence[phrase]
                ],
                "warning": "Frequency is only a signal; officer approval is required before this becomes a cue.",
            }
        )
    return proposals


def summarize(tickets: list[TicketHistory], pairs: list[QAPair], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    terms = extract_candidate_terms(pairs)
    messages = [message for ticket in tickets for message in ticket.messages]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ticket_count": len(tickets),
        "ticket_extraction_error_count": sum(1 for ticket in tickets if ticket.extraction_errors),
        "message_count": sum(len(ticket.messages) for ticket in tickets),
        "candidate_message_count": sum(1 for ticket in tickets for m in ticket.messages if m.role == "candidate"),
        "officer_message_count": sum(1 for ticket in tickets for m in ticket.messages if m.role == "officer"),
        "full_detail_message_count": sum(1 for message in messages if message.source_is_detail),
        "summary_source_message_count": sum(1 for message in messages if not message.source_is_detail),
        "truncated_suspected_message_count": sum(1 for message in messages if message.truncated_suspected),
        "qa_pair_count": len(pairs),
        "answered_pair_count": sum(1 for pair in pairs if pair.outcome == "answered"),
        "unanswered_pair_count": sum(1 for pair in pairs if pair.outcome == "unanswered"),
        "attachment_pair_count": sum(1 for pair in pairs if pair.attachment_count),
        "channels": dict(Counter(ticket.channel for ticket in tickets)),
        "statuses": dict(Counter(ticket.status or "unknown" for ticket in tickets)),
        "issue_tags": dict(Counter(tag for pair in pairs for tag in pair.issue_tags)),
        "tool_mentions": dict(Counter(tool for pair in pairs for tool in pair.tool_mentions)),
        "risk_flags": dict(Counter(flag for pair in pairs for flag in pair.risk_flags)),
        "quality_flags": dict(Counter(flag for pair in pairs for flag in pair.quality_flags)),
        "top_candidate_terms": terms.most_common(50),
        "proposal_count": len(proposals),
        "knowledge_proposal_count": sum(1 for item in proposals if item["type"] == "knowledge_update"),
        "routing_cue_proposal_count": sum(1 for item in proposals if item["type"] == "routing_cue"),
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ticket_history_from_dict(row: dict[str, Any]) -> TicketHistory:
    return TicketHistory(
        ticket_id=str(row["ticket_id"]),
        ticket_number=row.get("ticket_number"),
        subject=row.get("subject") or "",
        channel=row.get("channel") or "unknown",
        status=row.get("status"),
        created_time=row.get("created_time"),
        messages=[HistoryMessage(**message) for message in row.get("messages", [])],
        extraction_errors=list(row.get("extraction_errors") or []),
    )


def load_ticket_histories(path: Path) -> list[TicketHistory]:
    return [ticket_history_from_dict(row) for row in read_jsonl(path)]


def ticket_needs_retry(ticket: TicketHistory, *, require_full_detail: bool) -> bool:
    if ticket.extraction_errors:
        return True
    if require_full_detail:
        return any(not message.source_is_detail or message.truncated_suspected for message in ticket.messages)
    return False


def ticket_quality_score(ticket: TicketHistory, *, require_full_detail: bool) -> tuple[int, int, int]:
    low_quality_count = (
        sum(1 for message in ticket.messages if not message.source_is_detail or message.truncated_suspected)
        if require_full_detail
        else 0
    )
    return (
        0 if ticket.extraction_errors else 1,
        -low_quality_count,
        len(ticket.messages),
    )


def collapse_ticket_histories(
    tickets: list[TicketHistory],
    *,
    require_full_detail: bool,
) -> list[TicketHistory]:
    best: dict[str, tuple[int, TicketHistory]] = {}
    order: list[str] = []
    for index, ticket in enumerate(tickets):
        existing = best.get(ticket.ticket_id)
        if existing is None:
            best[ticket.ticket_id] = (index, ticket)
            order.append(ticket.ticket_id)
            continue
        _, current = existing
        if ticket_quality_score(ticket, require_full_detail=require_full_detail) >= ticket_quality_score(
            current,
            require_full_detail=require_full_detail,
        ):
            best[ticket.ticket_id] = (index, ticket)
    return [best[ticket_id][1] for ticket_id in order]


def dedupe_raw_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ticket in tickets:
        ticket_id = str(ticket.get("id") or "")
        if not ticket_id or ticket_id in seen:
            continue
        seen.add(ticket_id)
        unique.append(ticket)
    return unique


def ticket_from_mirror(mirror: HelpdeskTicketMirror) -> TicketHistory:
    return TicketHistory(
        ticket_id=mirror.zoho_ticket_id,
        ticket_number=mirror.ticket_number,
        subject=mirror.subject or "",
        channel=mirror.channel or "unknown",
        status=mirror.zoho_status,
        created_time=mirror.ticket_created_at.isoformat() if mirror.ticket_created_at else None,
        messages=[],
        extraction_errors=[],
    )


def load_local_tickets(limit: int | None) -> list[TicketHistory]:
    with Session(engine) as session:
        statement = select(HelpdeskTicketMirror).order_by(HelpdeskTicketMirror.ticket_created_at.desc())
        if limit:
            statement = statement.limit(limit)
        return [ticket_from_mirror(row) for row in session.exec(statement).all()]


def retry_call(label: str, retries: int, delay_seconds: float, func):
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return func()
        except Exception as exc:
            if is_fatal_zoho_auth_error(exc):
                raise FatalExtractionError(f"{label}: {safe_error_message(exc)}") from exc
            last_error = exc
            if attempt > retries:
                break
            print(
                f"{label} failed on attempt {attempt}; retrying in {delay_seconds:g}s: {safe_error_message(exc)}",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def is_fatal_zoho_auth_error(exc: Exception) -> bool:
    if isinstance(exc, ZohoAuthError):
        return True
    message = safe_error_message(exc).lower()
    if "/oauth/v2/token" in message:
        return True
    if "client error" in message and any(status in message for status in ("401", "403")):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is None:
            return False
        return response.status_code in {401, 403} or (
            response.status_code == 400 and "/oauth/v2/token" in (response.url or "")
        )
    return False


def fetch_zoho_tickets(
    limit: int,
    page_size: int,
    retries: int,
    retry_delay: float,
    request_timeout: float,
) -> list[dict[str, Any]]:
    settings = get_settings()
    client = build_zoho_desk_client(settings, request_timeout=request_timeout)
    tickets: list[dict[str, Any]] = []
    for offset in range(0, limit, page_size):
        page_limit = min(page_size, limit - offset)
        print(f"Fetching Zoho ticket page from={offset} limit={page_limit}", file=sys.stderr)
        page = retry_call(
            f"tickets page from={offset}",
            retries,
            retry_delay,
            lambda: client.list_tickets(
                department_id=settings.zoho_department_id,
                limit=page_limit,
                from_index=offset,
            ).get("data", []),
        )
        tickets.extend(page)
        if len(page) < page_limit:
            break
    return tickets


def load_or_fetch_zoho_tickets(
    cache_path: Path,
    resume: bool,
    limit: int,
    page_size: int,
    retries: int,
    retry_delay: float,
    request_timeout: float,
) -> list[dict[str, Any]]:
    if resume and cache_path.exists():
        cached = read_jsonl(cache_path)
        if cached:
            print(f"Loaded {len(cached)} cached Zoho ticket summaries from {cache_path}", file=sys.stderr)
            return cached[:limit]
    tickets = fetch_zoho_tickets(limit, page_size, retries, retry_delay, request_timeout)
    write_jsonl(cache_path, tickets)
    return tickets


def history_from_ticket(ticket: dict[str, Any]) -> TicketHistory:
    return TicketHistory(
        ticket_id=str(ticket["id"]),
        ticket_number=ticket.get("ticketNumber"),
        subject=ticket.get("subject") or "",
        channel=ticket.get("channel") or "unknown",
        status=ticket.get("status"),
        created_time=ticket.get("createdTime"),
    )


def hydrate_one_zoho_ticket(
    ticket: dict[str, Any],
    retries: int,
    retry_delay: float,
    thread_detail_mode: str,
    client=None,
    request_timeout: float = 30,
) -> TicketHistory:
    if client is None:
        client = build_zoho_desk_client(get_settings(), request_timeout=request_timeout)
    history = history_from_ticket(ticket)
    try:
        summaries = retry_call(
            f"threads for ticket {history.ticket_id}",
            retries,
            retry_delay,
            lambda: client.list_ticket_threads(history.ticket_id).get("data", []),
        )
    except FatalExtractionError:
        raise
    except Exception as exc:
        history.extraction_errors.append(f"thread_list_failed: {safe_error_message(exc)}")
        return history

    messages: list[HistoryMessage] = []
    for summary in summaries:
        detail = summary
        needs_detail = (
            thread_detail_mode == "always"
            or (thread_detail_mode == "missing" and not _first_text_field(summary))
        )
        if needs_detail:
            try:
                thread_id = str(summary.get("id") or "")
                detail = retry_call(
                    f"thread {thread_id} for ticket {history.ticket_id}",
                    retries,
                    retry_delay,
                    lambda: client.get_thread(history.ticket_id, thread_id),
                )
                detail["__source_is_detail"] = True
            except FatalExtractionError:
                raise
            except Exception:
                history.extraction_errors.append(f"thread_detail_failed: {str(summary.get('id') or '')}")
                detail = summary
        message = message_from_thread(detail)
        if message:
            messages.append(message)
    history.messages = sort_messages(messages)
    return history


def hydrate_zoho_threads(
    raw_tickets: list[dict[str, Any]],
    retries: int,
    retry_delay: float,
    thread_detail_mode: str,
    stream_path: Path | None = None,
    skip_ticket_ids: set[str] | None = None,
    workers: int = 1,
    request_timeout: float = 30,
) -> list[TicketHistory]:
    raw_tickets = dedupe_raw_tickets(raw_tickets)
    pending = [
        (number, ticket)
        for number, ticket in enumerate(raw_tickets, start=1)
        if not skip_ticket_ids or str(ticket["id"]) not in skip_ticket_ids
    ]
    histories: list[TicketHistory] = []

    if workers <= 1:
        client = build_zoho_desk_client(get_settings(), request_timeout=request_timeout)
        for number, ticket in pending:
            preview = history_from_ticket(ticket)
            print(
                f"Fetching threads for ticket {number}/{len(raw_tickets)} #{preview.ticket_number or preview.ticket_id}",
                file=sys.stderr,
            )
            history = hydrate_one_zoho_ticket(
                ticket,
                retries,
                retry_delay,
                thread_detail_mode,
                client=client,
                request_timeout=request_timeout,
            )
            histories.append(history)
            if stream_path:
                append_jsonl(stream_path, asdict(history))
        return histories

    print(f"Fetching threads with {workers} workers for {len(pending)} remaining tickets", file=sys.stderr)
    client = build_zoho_desk_client(get_settings(), request_timeout=request_timeout)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                hydrate_one_zoho_ticket,
                ticket,
                retries,
                retry_delay,
                thread_detail_mode,
                client,
                request_timeout,
            ): (number, ticket)
            for number, ticket in pending
        }
        completed = 0
        for future in as_completed(futures):
            completed += 1
            number, ticket = futures[future]
            try:
                history = future.result()
            except FatalExtractionError:
                for pending_future in futures:
                    pending_future.cancel()
                raise
            except Exception as exc:
                history = history_from_ticket(ticket)
                history.extraction_errors.append(f"worker_failed: {safe_error_message(exc)}")
            print(
                f"Completed threads for ticket {number}/{len(raw_tickets)} "
                f"#{history.ticket_number or history.ticket_id} ({completed}/{len(pending)} remaining batch)",
                file=sys.stderr,
            )
            histories.append(history)
            if stream_path:
                append_jsonl(stream_path, asdict(history))
    return histories


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze historical helpdesk Q&A for officer-reviewed KB/cue proposals.")
    parser.add_argument("--source", choices=["local", "zoho"], default="local")
    parser.add_argument("--max-tickets", type=int, default=3000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=3.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument(
        "--fast-pass",
        action="store_true",
        help="Move quickly through unstable Zoho tickets by using shorter request timeouts and fewer retries.",
    )
    parser.add_argument(
        "--thread-detail-mode",
        choices=["always", "missing", "never"],
        default="missing",
        help="Fetch every thread detail, only missing-text details, or only ticket thread summaries.",
    )
    parser.add_argument("--out-dir", default="data/helpdesk-history")
    parser.add_argument("--kb-dir", default=None)
    parser.add_argument("--resume", action="store_true", help="Resume a previous Zoho extraction from out-dir/tickets.jsonl.")
    parser.add_argument(
        "--keep-incomplete-on-resume",
        action="store_true",
        help="When resuming, skip previously written tickets even if they had extraction errors or lower-quality summary fallback.",
    )
    parser.add_argument("--workers", type=int, default=1, help="Concurrent ticket thread fetch workers for Zoho source.")
    return parser.parse_args()


def safe_error_message(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.url:
        message = f"{message} for url: {exc.response.url}"
    redactions = (
        (r"(refresh_token=)[^&\s]+", r"\1[REDACTED]"),
        (r"(client_secret=)[^&\s]+", r"\1[REDACTED]"),
        (r"(client_id=)[^&\s]+", r"\1[REDACTED]"),
        (r"(Authorization['\"]?:\s*['\"]?Zoho-oauthtoken\s+)[^'\"\s]+", r"\1[REDACTED]"),
    )
    for pattern, replacement in redactions:
        message = re.sub(pattern, replacement, message, flags=re.I)
    return message


def main() -> int:
    args = parse_args()
    effective_retries = min(args.retries, 1) if args.fast_pass else args.retries
    effective_retry_delay = min(args.retry_delay, 2.0) if args.fast_pass else args.retry_delay
    effective_request_timeout = min(args.request_timeout, 10.0) if args.fast_pass else args.request_timeout
    settings = get_settings()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tickets_path = out_dir / "tickets.jsonl"

    try:
        if args.source == "zoho":
            if not args.resume:
                for stale in (
                    tickets_path,
                    out_dir / "qa_pairs.jsonl",
                    out_dir / "officer_review_proposals.jsonl",
                    out_dir / "summary.json",
                    out_dir / "zoho_ticket_summaries.jsonl",
                ):
                    stale.unlink(missing_ok=True)
            raw_tickets = load_or_fetch_zoho_tickets(
                out_dir / "zoho_ticket_summaries.jsonl",
                args.resume,
                args.max_tickets,
                args.page_size,
                effective_retries,
                effective_retry_delay,
                effective_request_timeout,
            )
            raw_tickets = dedupe_raw_tickets(raw_tickets)
            require_full_detail = args.thread_detail_mode == "always"
            existing = load_ticket_histories(tickets_path) if args.resume else []
            existing = collapse_ticket_histories(existing, require_full_detail=require_full_detail)
            if args.keep_incomplete_on_resume:
                existing_ids = {ticket.ticket_id for ticket in existing}
            else:
                existing_ids = {
                    ticket.ticket_id
                    for ticket in existing
                    if not ticket_needs_retry(ticket, require_full_detail=require_full_detail)
                }
            retained_existing = [ticket for ticket in existing if ticket.ticket_id in existing_ids]
            if args.resume and existing:
                retry_count = len(existing) - len(existing_ids)
                if retry_count:
                    print(f"Retrying {retry_count} incomplete or lower-quality ticket extractions", file=sys.stderr)
                write_jsonl(tickets_path, (asdict(ticket) for ticket in retained_existing))
            hydrate_zoho_threads(
                raw_tickets,
                effective_retries,
                effective_retry_delay,
                args.thread_detail_mode,
                stream_path=tickets_path,
                skip_ticket_ids=existing_ids,
                workers=args.workers,
                request_timeout=effective_request_timeout,
            )
            tickets = collapse_ticket_histories(load_ticket_histories(tickets_path), require_full_detail=require_full_detail)
            write_jsonl(tickets_path, (asdict(ticket) for ticket in tickets))
        else:
            tickets = load_local_tickets(args.max_tickets)
    except KeyboardInterrupt:
        print("Historical analysis interrupted before final reports were regenerated.")
        return 130
    except FatalExtractionError as exc:
        print(f"Historical analysis stopped before final reports were regenerated: {exc}")
        return 130
    except Exception as exc:
        print(f"Historical analysis failed while reading {args.source}: {safe_error_message(exc)}")
        return 1

    pairs = [pair for ticket in tickets for pair in qa_pairs_from_ticket(ticket)]
    kb_chunks = load_current_kb_texts(args.kb_dir or settings.kb_dir)
    knowledge_proposals = build_proposals(pairs, kb_chunks)
    cue_proposals = build_cue_candidates(pairs)
    proposals = [*knowledge_proposals, *cue_proposals]
    summary = summarize(tickets, pairs, proposals)

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if args.source != "zoho":
        write_jsonl(tickets_path, (asdict(ticket) for ticket in tickets))
    write_jsonl(out_dir / "qa_pairs.jsonl", (asdict(pair) for pair in pairs))
    write_jsonl(out_dir / "officer_review_proposals.jsonl", proposals)

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote analysis files to {out_dir.resolve()}")
    if args.source == "local" and summary["qa_pair_count"] == 0:
        print("Local mirror has ticket metadata but no thread bodies. Run with --source zoho to build real Q&A pairs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
