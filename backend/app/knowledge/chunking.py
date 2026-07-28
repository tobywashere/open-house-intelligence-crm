"""Markdown -> Chunk splitting.

Splits a document on its headings (any level, ``#`` .. ``######``). Each
resulting section becomes one Chunk carrying the source filename, the
section's own heading text, a breadcrumb (``H1 > H2 > H3``) built from the
heading stack active at that point, and the section body text.

A section whose body exceeds ``MAX_CHUNK_CHARS`` is further split on
paragraph boundaries (blank-line-separated) so no single chunk dominates a
BM25 index — but a paragraph is never split mid-sentence/mid-paragraph.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 1500

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass
class Chunk:
    doc: str
    heading: str
    breadcrumb: str
    text: str


def _split_paragraphs(text: str, cap: int) -> list[str]:
    """Greedily pack paragraphs (blank-line separated) into pieces <= cap chars,
    never splitting a paragraph itself. A single paragraph longer than cap is
    kept whole (we cap sections, not sentences)."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p)
        if current and current_len + 2 + p_len > cap:
            pieces.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(p)
        current_len += p_len + (2 if current_len else 0)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_markdown(text: str, doc: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split a markdown document into heading-scoped Chunks."""
    lines = text.splitlines()

    # sections: list of (level, heading_text, body_lines)
    sections: list[tuple[int, str, list[str]]] = []
    stack: list[tuple[int, str]] = []  # active heading path before the first heading

    current_level = 0
    current_heading = ""
    current_body: list[str] = []
    seen_heading = False

    def flush():
        if seen_heading or current_body:
            sections.append((current_level, current_heading, list(current_body)))

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush()
            current_level = len(m.group(1))
            current_heading = m.group(2)
            current_body = []
            seen_heading = True
        else:
            current_body.append(line)
    flush()

    chunks: list[Chunk] = []
    breadcrumb_stack: list[tuple[int, str]] = []

    for level, heading, body_lines in sections:
        if level:
            # pop deeper-or-equal levels, then push this heading
            breadcrumb_stack = [(lv, h) for lv, h in breadcrumb_stack if lv < level]
            breadcrumb_stack.append((level, heading))
        breadcrumb = " > ".join(h for _, h in breadcrumb_stack) if breadcrumb_stack else heading
        body = "\n".join(body_lines).strip("\n")
        if not body.strip():
            # heading with no body text (e.g. a bare H1 title) carries nothing
            # to search on its own — its breadcrumb still applies to children.
            continue
        if len(body) <= max_chars:
            chunks.append(Chunk(doc=doc, heading=heading or doc, breadcrumb=breadcrumb, text=body.strip()))
        else:
            for piece in _split_paragraphs(body, max_chars):
                chunks.append(Chunk(doc=doc, heading=heading or doc, breadcrumb=breadcrumb, text=piece.strip()))

    return chunks
