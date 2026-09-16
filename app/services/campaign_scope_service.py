"""Inbound campaign scoping for the Calling Agent.

Given the campaign name a caller says, decide whether it's a valid, currently
active campaign and what KB scope to retrieve against. Kept separate from the
outbound-heavy campaign_service.
"""

import re
from datetime import datetime
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.core.vocabulary import ALLOWED_TOOLS
from app.models.campaign import Campaign

# Shorter than this and a garbled ASR fragment starts matching sponsors by
# accident (a single letter can appear in several campaign names).
MIN_MATCH_LENGTH = 3

# How close a garbled phrase must be to a campaign name before we offer it back
# as "did you mean...?". Tuned against real ASR manglings of the live campaign
# names: the worst true positive ("Anguti" for "Dangote") scores 0.62, while
# unrelated sponsors ("Shell", "Total Energies", "Access Bank") stay under 0.57.
SUGGEST_THRESHOLD = 0.55

# A suggestion must also plausibly be the SPONSOR the caller said — the leading
# word of the campaign name. Without this, a phrase matches on generic tail
# words ("Total Energies" -> "Matrix Energy Group") and we suggest nonsense.
HEAD_SIMILARITY_FLOOR = 0.5

# More than a few options is unusable read aloud on a phone call.
MAX_SUGGESTIONS = 3

_NON_WORD = re.compile(r"[^a-z0-9]+")

# Filler a caller wraps the name in ("the Dangote assessment"). Stripped before
# comparing single words against a sponsor, so "assessment" can't be the thing
# that carries a match.
_FILLER = {
    "assessment", "assessments", "test", "tests", "exam", "exams",
    "campaign", "programme", "program", "survey", "feedback", "csat",
    "the", "for", "my", "a", "an", "of", "and",
}


def _normalize(value: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    The caller's phrase arrives from speech recognition, which produces plain
    words — no em dashes, hyphens or ampersands — while stored names are typed
    by staff and full of them. Normalising both sides is what lets a caller
    saying "Dangote PRP Graduate Trainee Feedback" match the stored
    "Dangote PRP Graduate Trainee — Feedback".
    """
    return _NON_WORD.sub(" ", (value or "").lower()).strip()


def _contains_words(haystack: str, needle: str) -> bool:
    """Whether `needle` appears in `haystack` as a run of WHOLE words.

    Word-boundary matching stops "v" or "energy" resolving to an unrelated
    sponsor, which would hand the caller another campaign's knowledge base.
    """
    return f" {needle} " in f" {haystack} "


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _match_score(phrase: str, name: str) -> float:
    """How likely the caller was trying to say `name`, from 0 to 1.

    Speech recognition mangles sponsor names ("Dangote" comes back as
    "Anguti"), so whole-word matching alone dead-ends the call. This scores a
    garbled phrase against a stored name three ways and takes the best: the
    caller may say the whole name, just the sponsor prefix, or one mangled
    word. Both sides are already normalised.

    Gated on the sponsor headword: a phrase that looks nothing like the leading
    word of the name scores 0 even if its tail lines up, because a caller
    naming a different company ("Total Energies") must not be offered an
    unrelated campaign that happens to share a generic word ("Matrix Energy").
    """
    name_tokens = name.split()
    phrase_tokens = [t for t in phrase.split() if t not in _FILLER] or phrase.split()
    if not phrase_tokens or not name_tokens:
        return 0.0

    head = name_tokens[0]
    if max(_similarity(t, head) for t in phrase_tokens) < HEAD_SIMILARITY_FLOOR:
        # Only bail if the phrase as a whole doesn't open with the sponsor
        # either — "dangote prp graduate trainee" is a legitimate full name.
        if _similarity(phrase[: len(head)], head) < HEAD_SIMILARITY_FLOOR:
            return 0.0

    best = _similarity(phrase, name)
    for k in range(1, min(3, len(name_tokens)) + 1):
        best = max(best, _similarity(phrase, " ".join(name_tokens[:k])))
    for token in phrase_tokens:
        best = max(best, _similarity(token, head))
    return best


def _suggestions(phrase: str, campaigns: list[Campaign]) -> list[Campaign]:
    """Campaigns close enough to the garbled phrase to read back for confirmation."""
    scored = [(_match_score(phrase, _normalize(c.name)), c) for c in campaigns]
    close = [(score, c) for score, c in scored if score >= SUGGEST_THRESHOLD]
    close.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in close[:MAX_SUGGESTIONS]]


def _spoken_tool_name(phrase: str) -> str | None:
    """The assessment tool the caller named, if that's all they said.

    Candidates aren't meant to know FOT from Test Haven, but they read it on
    screen and say it. Recognising it lets the agent redirect ("that's the
    platform — which organisation?") instead of dead-ending on not_found.
    """
    for tool in ALLOWED_TOOLS:
        if phrase == _normalize(tool):
            return tool
    return None


def campaign_inbound_available(campaign: Campaign, now: datetime) -> bool:
    """Is this campaign available to inbound callers right now?

    False if switched off, or before/after its optional active window.
    """
    if not campaign.inbound_active:
        return False
    if campaign.active_from and now < campaign.active_from:
        return False
    if campaign.active_until and now > campaign.active_until:
        return False
    return True


def campaign_scope(campaign: Campaign) -> str:
    """The KB tag to filter on for this campaign (its kb_scope, else its id)."""
    return campaign.kb_scope or campaign.id


def tool_name_to_scope(tool_name: str) -> str:
    """Normalize a tool name ("Test Haven") into its KB scope tag
    ("test-haven") — the same slugging every other scope tag uses."""
    return _NON_WORD.sub("-", tool_name.lower()).strip("-")


def campaign_tool_scope(campaign: Campaign) -> str | None:
    """The KB scope for this campaign's assessment tool (FOT/Test Haven/
    Scholastica), resolved from the campaign's own tool_name — never asked
    of the caller, who doesn't know or care about this distinction. None if
    the campaign has no tool_name set.

    This is a THIRD KB tier alongside general and campaign: tool content
    (e.g. "can I use a phone instead of a laptop?") applies to every campaign
    using that tool, not just one client's campaign.
    """
    if not campaign.tool_name:
        return None
    return tool_name_to_scope(campaign.tool_name)


def resolve_campaign(session: Session, name: str, now: datetime | None = None) -> dict:
    """Resolve a spoken campaign name to a scoping decision.

    Returns {status, name, scope, tool_scope, options, available,
    available_count} where status is one of:
      - "found":     exactly one active campaign matched -> use `scope` (and
                     `tool_scope`, which may be None if the campaign has no
                     tool_name set)
      - "inactive":  matched, but not currently available -> `scope`/`tool_scope` are None
      - "ambiguous": more than one campaign matched -> `options` holds their
                     names so the agent can ask which one
      - "did_you_mean": nothing matched outright, but the phrase is close to
                     one or more real campaigns (speech recognition garbles
                     sponsor names) -> `options` holds them to read back
      - "tool_not_campaign": the caller named the assessment platform (FOT,
                     Test Haven, Scholastica) rather than an organisation
      - "not_found": nothing matched -> `available` lists current campaigns so
                     the agent can offer options instead of dead-ending

    Matching is case-insensitive and punctuation-insensitive, because the
    phrase comes from speech recognition: stored names like "Dangote PRP
    Graduate Trainee — Feedback" contain an em dash that ASR will never
    produce, so both sides are normalised to plain words before comparing. An
    exact normalised match wins outright; otherwise the phrase must appear as a
    run of whole words in the name ("Dangote" matches, a stray letter does
    not). Only when that finds nothing do we fall back to fuzzy suggestions,
    which are always read back for confirmation and never resolve a scope on
    their own.
    """
    now = now or datetime.utcnow()
    phrase = _normalize(name)

    # Only campaigns that are actually available are candidates. An archived
    # campaign from last cycle must not make this cycle's ambiguous.
    available = [
        c
        for c in session.exec(select(Campaign)).all()
        if campaign_inbound_available(c, now)
    ]

    def result(status, *, name=None, scope=None, tool_scope=None, options=None,
               offer_available=False):
        return {
            "status": status,
            "name": name,
            "scope": scope,
            "tool_scope": tool_scope,
            "options": options or [],
            "available": [c.name for c in available] if offer_available else [],
            "available_count": len(available) if offer_available else 0,
        }

    # Below this, a garbled ASR fragment starts matching campaigns by accident
    # (a single letter can "match" several sponsors).
    if len(phrase) < MIN_MATCH_LENGTH:
        return result("not_found", offer_available=True)

    exact = [c for c in available if _normalize(c.name) == phrase]
    matches = exact or [c for c in available if _contains_words(_normalize(c.name), phrase)]

    if matches:
        if len(matches) > 1:
            return result("ambiguous", options=[c.name for c in matches])
        campaign = matches[0]
        return result(
            "found",
            name=campaign.name,
            scope=campaign_scope(campaign),
            tool_scope=campaign_tool_scope(campaign),
        )

    # Distinguish "exists but switched off" from "no such campaign", so the
    # agent can say something useful.
    inactive = [
        c
        for c in session.exec(select(Campaign)).all()
        if _contains_words(_normalize(c.name), phrase)
    ]
    if len(inactive) == 1:
        return result("inactive", name=inactive[0].name)

    # Nothing matched literally. Before giving up, offer near-misses — this is
    # what stops a mangled "Anguti" from ending the call instead of becoming
    # "did you mean Dangote PRP Graduate Trainee?".
    close = _suggestions(phrase, available)
    if close:
        return result("did_you_mean", options=[c.name for c in close])

    tool = _spoken_tool_name(phrase)
    if tool:
        return result("tool_not_campaign", name=tool, offer_available=True)

    return result("not_found", offer_available=True)
