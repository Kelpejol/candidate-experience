"""Convert a Word (.docx) KB document into the heading-per-question markdown
text `voice_kb_ingest.chunk_markdown` / the Helpdesk KB chunker already
expect, so nothing downstream changes — only how we get from "file in
SharePoint" to "heading-structured text" changes.

The authoring standard this enforces (see docs/blocked-items-and-helpdesk-
plan.md, "SharePoint Knowledge Base Connector"):
  - File must be .docx (not .doc, not .pdf) — python-docx only reads .docx,
    and a PDF has no reliable heading metadata to extract.
  - Every question is its own "Heading 1" or "Heading 2" styled paragraph in
    Word — either level is fine, they're treated identically (there is no
    parent/child nesting downstream). Anything deeper (Heading 3+) is treated
    as ordinary body text, not a new question boundary — kept simple on
    purpose: officers only ever need to remember two style names.
  - The heading does not need to be phrased as a literal question — a
    self-contained statement of the topic works just as well ("I forgot my
    password" is a real, working example already in kb_voice/).
  - The answer is plain paragraph text directly under its heading.
  - Table content is not read — only top-level paragraphs.
"""

import io
import re
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError

# Deliberately [12] only, not [1-6] — see the module docstring on why Heading
# 3+ is treated as body text rather than recognized as a question boundary.
_HEADING_STYLE = re.compile(r"^heading\s*([12])$", re.IGNORECASE)


def docx_to_markdown(data: bytes) -> tuple[str, list[str]]:
    """Convert a .docx file's bytes into heading-per-question markdown text.

    Returns (markdown_text, warnings). A heading with no answer text before
    the next heading (or the end of the document) is DROPPED rather than
    passed through as a near-empty chunk — that's almost always a section
    title used decoratively, not a real question, and indexing it would just
    add a useless, low-quality entry to the KB. Each drop is reported as a
    warning so whoever ran the reindex can go fix the source document.

    Raises ValueError if `data` isn't a valid .docx file (e.g. someone
    uploaded a .doc or a .pdf) — a clear, specific error rather than
    python-docx's own low-level zip/XML exception.
    """
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile) as exc:
        # .docx is a zip archive; a real .doc or .pdf isn't one at all (raises
        # BadZipFile), while a zip that just isn't a valid Office package
        # raises PackageNotFoundError — both mean "not a real .docx".
        raise ValueError(
            "Not a valid .docx file. Only .docx is supported — a .doc "
            "(legacy Word format) or a .pdf must be re-saved as .docx first."
        ) from exc

    # Pass 1: group paragraphs into (level, heading, body_lines) sections.
    # Body text before the first heading has no question to attach to, so
    # it's dropped rather than carried through as one of chunk_markdown's own
    # odd heading-less chunks.
    sections: list[tuple[int, str, list[str]]] = []
    saw_any_heading = False

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        match = _HEADING_STYLE.match(paragraph.style.name or "")

        if match:
            if not text:
                continue  # an empty heading carries no question — skip it
            saw_any_heading = True
            sections.append((int(match.group(1)), text, []))
        elif text and sections:
            sections[-1][2].append(text)

    # Pass 2: drop any heading that never got an answer, and build the text.
    warnings: list[str] = []
    lines: list[str] = []

    for level, heading, body in sections:
        if not body:
            warnings.append(
                f"Heading {heading!r} has no answer text before the next "
                "heading (or the end of the document) — dropped. Is this "
                "meant to be a section title rather than a question?"
            )
            continue
        lines.append(f"{'#' * level} {heading}")
        lines.extend(body)

    if not saw_any_heading:
        warnings.append(
            "No Heading 1 / Heading 2 paragraphs found in this document — "
            "nothing will be indexed from it."
        )

    return "\n".join(lines), warnings
