"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from .models import Document, Page


def clean(doc: Document) -> Document:
    """Strip headers and footers that repeat on every page; keep headings and
    section boundaries, because chunking and citations depend on them."""
    pages = doc.pages
    if not pages:
        return doc

    repeated = _find_repeated_lines(pages)
    cleaned: list[Page] = []
    for page in pages:
        text = _remove_repeated(page.text, repeated)
        text = _normalize_whitespace(text)
        cleaned.append(Page(source=page.source, page=page.page, text=text, ocr=page.ocr))

    return replace(doc, pages=cleaned)  # keeps every other field, doc_id included


def _find_repeated_lines(pages: list[Page]) -> set[str]:
    """Find lines appearing in the first or last 3 lines of 50%+ of pages."""
    if len(pages) < 2:
        return set()

    # At least 2: on a two-page document "half the pages" is one page, which would treat
    # every line near the top or bottom of either page as a header and delete it.
    threshold = max(2, len(pages) / 2)
    head_counter: Counter[str] = Counter()
    tail_counter: Counter[str] = Counter()

    for page in pages:
        lines = page.text.splitlines()
        # Sets, so each page counts a line once: "repeated" means on many pages, not
        # many times on one page.
        head_counter.update({line.strip() for line in lines[:3] if line.strip()})
        tail_counter.update({line.strip() for line in lines[-3:] if line.strip()})

    # A line repeating on >=50% of pages is a running header/footer, full stop -
    # a genuine section heading marks the start of ONE section and would never
    # repeat across half a multi-page document. Markdown headings ("#...") are
    # the one exception: those come from the fixture corpus's front matter-style
    # structure, not from a scanned/printed running header, so they're protected.
    repeated: set[str] = set()
    for line, count in head_counter.items():
        if count >= threshold and not line.startswith("#"):
            repeated.add(line)
    for line, count in tail_counter.items():
        if count >= threshold and not line.startswith("#"):
            repeated.add(line)
    return repeated


def _is_table_line(line: str) -> bool:
    return "|" in line or "\t" in line


def _remove_repeated(text: str, repeated: set[str]) -> str:
    if not repeated:
        return text
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if line.strip() in repeated and not _is_table_line(line):
            continue
        out.append(line)
    return "\n".join(out)


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
