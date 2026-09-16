"""Shared markdown chunking for both KBs: one heading (any level 1-6) = one
retrievable chunk.

This is the single unified authoring rule for the voice KB
(`voice_kb_ingest`) and the Helpdesk KB (`helpdesk_kb_service`) — an officer
only ever learns one convention: put every question under a Heading 1 or
Heading 2 style in Word (or a '#'/'##' line in markdown), and it works
identically in both systems. Extracted here because both domains need the
exact same algorithm, not two versions that could drift apart.
"""

import re

_HEADING = re.compile(r"^#{1,6}\s")   # a markdown heading line, e.g. "## Title"


def chunk_markdown(text: str) -> list[str]:
    """Split markdown into one chunk per heading section (heading + its body)."""
    chunks: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        if _HEADING.match(line):          # a new heading starts a new chunk
            if current:
                chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:                            # don't forget the last section
        chunks.append("\n".join(current).strip())

    return [c for c in chunks if c]        # drop any empties


def heading_of(chunk: str) -> str:
    """The chunk's heading text, with the leading #'s stripped."""
    return re.sub(r"^#{1,6}\s+", "", chunk.splitlines()[0]).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]
