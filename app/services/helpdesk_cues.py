"""Approved identification context; no inferred campaign or keyword mappings."""

import re
import hashlib
import json
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.vocabulary import ALLOWED_TOOLS


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def contains(text: str, phrase: str) -> bool:
    return f" {normalize(phrase)} " in f" {normalize(text)} "


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    phrase: str = Field(min_length=2)
    tools: list[str] = Field(min_length=1)
    campaign: str | None = None
    conditions: str = ""
    exclusive: bool = False
    status: str = "pending"
    approved_by: str | None = None
    approved_at: date | None = None
    approved_digest: str | None = None
    valid_until: date | None = None
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tools(self):
        if not set(self.tools) <= set(ALLOWED_TOOLS):
            raise ValueError("Cue references an unknown tool")
        if self.exclusive and len(set(self.tools)) != 1:
            raise ValueError("An exclusive cue must identify one tool")
        if self.status == "approved" and not (self.approved_by and self.approved_at):
            raise ValueError("Approved cues require reviewer and approval date")
        return self

    def content_digest(self) -> str:
        content = self.model_dump(mode="json", exclude={"status", "approved_by", "approved_at", "approved_digest"})
        return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CueRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cues: list[Cue] = Field(default_factory=list)

    def approved(self) -> list[Cue]:
        today = date.today()
        return [c for c in self.cues if c.status == "approved"
                and c.approved_at <= today
                and c.approved_digest == c.content_digest()
                and (c.valid_until is None or c.valid_until >= today)]


def load_registry(path: str) -> CueRegistry:
    return CueRegistry.model_validate_json(Path(path).read_text())


def resolve_tools(messages: list[dict], latest_id: str, subject: str, registry: CueRegistry) -> dict:
    """Latest explicit candidate statement wins; shared/conditional cues only suggest.

    Do not infer tool identity from the classifier or our own outgoing questions.
    Negated names are kept as uncertainty rather than silently asserting a match.
    """
    candidates = [m for m in messages if m["role"] in {"candidate", "candidate_attachment"}]
    latest_sources = [m for m in candidates if m["id"] == latest_id or m["id"].startswith(latest_id + ":ocr:")]
    screenshot_names = {t for m in latest_sources if m["role"] == "candidate_attachment" for t in ALLOWED_TOOLS if contains(m["text"], t)}
    written_names = {t for m in latest_sources if m["role"] == "candidate" for t in ALLOWED_TOOLS if contains(m["text"], t)}
    if screenshot_names and written_names and screenshot_names != written_names:
        return {"tool": None, "options": sorted(screenshot_names | written_names),
                "source_id": latest_id, "evidence": "\n".join(m["text"] for m in latest_sources), "conflict": True}
    sources = list(reversed(candidates)) + [{"id": "subject", "text": subject}]
    for message in sources:
        text = message["text"]
        if message["id"] == latest_id and re.search(r"\b(different|another|wrong) (?:tool|platform)|not (?:that|the same) (?:tool|platform)", text, re.I):
            return {"tool": None, "options": [], "source_id": message["id"], "evidence": text, "conflict": True}
        names = [name for name in sorted(ALLOWED_TOOLS) if contains(text, name)]
        if names:
            uncertain = bool(re.search(r"\b(maybe|unsure|not sure|is it|or)\b", text, re.I))
            negated = {name for name in names if re.search(
                    r"\b(?:not|isn't|wasn't)\s+(?:(?:using|on|the)\s+)*" + re.escape(name),
                    text, re.I,
                )}
            positive = [name for name in names if name not in negated]
            certain = len(positive) == 1 and not uncertain
            options = positive or sorted(set(ALLOWED_TOOLS) - negated)
            return {"tool": positive[0] if certain else None,
                    "options": options, "source_id": message["id"],
                    "evidence": text, "conflict": not certain}
        matches = [c for c in registry.approved() if contains(text, c.phrase)]
        if matches:
            options = sorted({t for c in matches for t in c.tools})
            certain = len(options) == 1 and all(c.exclusive and not c.conditions for c in matches)
            return {"tool": options[0] if certain else None, "options": options,
                    "source_id": message["id"], "evidence": text,
                    "conflict": not certain}
        # Fuzzy matches are suggestions only, even when there is just one.
        words = normalize(text).split()
        suggestions = []
        choices = [("tool:" + t, t, [t]) for t in sorted(ALLOWED_TOOLS)]
        choices.extend((c.id, c.campaign or c.phrase, c.tools) for c in registry.approved())
        for key, label, tools in choices:
            phrase = normalize(label)
            n = len(phrase.split())
            spans = [" ".join(words[i:i + size]) for size in {1, n, max(1, n - 1)} for i in range(len(words))]
            if any(len(span) >= 4 and (SequenceMatcher(None, span, phrase).ratio() >= 0.82
                   or (" " not in span and phrase.startswith(span + " "))) for span in spans):
                suggestions.append({"id": key, "label": label, "tools": tools})
        if suggestions:
            return {"tool": None, "options": sorted({t for s in suggestions for t in s["tools"]}),
                    "suggestions": suggestions[:3], "source_id": message["id"], "evidence": text, "conflict": True}
    return {"tool": None, "options": [], "source_id": None, "evidence": "", "conflict": False}
