"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import replace

from .models import Document, Page

# Bump whenever a change here can alter the cleaned text. It is part of the re-embedding
# fingerprint (ARCHITECTURE 3.6). "1" did not recognise numbered running footers.
CLEANER_VERSION = "2"

EDGE_LINES = 3              # running headers and footers sit in these first and last lines
MIN_RUNNING_PAGES = 3       # a numbered line must recur this often to be a footer
_NUMBER = re.compile(r"[0-9]+")
_ROMAN = re.compile(r"[IVXL]+")


def clean(doc: Document) -> Document:
    """Strip headers and footers that repeat on every page; keep headings and
    section boundaries, because chunking and citations depend on them."""
    pages = doc.pages
    if not pages:
        return doc

    repeated = _find_repeated_lines(pages)
    footers = _find_numbered_footers(pages)
    cleaned: list[Page] = []
    for page, footer_lines in zip(pages, footers):
        text = "\n".join(line for i, line in enumerate(page.text.splitlines())
                         if i not in footer_lines or _is_table_line(line))
        text = _remove_repeated(text, repeated)
        text = _normalize_whitespace(text)
        cleaned.append(replace(page, text=text))

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
        head_counter.update({line.strip() for line in lines[:EDGE_LINES] if line.strip()})
        tail_counter.update({line.strip() for line in lines[-EDGE_LINES:] if line.strip()})

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


# ---------------------------------------------------------------- numbered footers
def _find_numbered_footers(pages: list[Page]) -> list[set[int]]:
    """For each page, the line numbers of running footers that carry the page number,
    such as "4 Student Handbook 2026". The exact-repeat rule above misses them, because
    no two pages have the same line, and the chunker then reads them as headings.

    A page number is the number that grows by one when the page does. So:
    1. Find the offset (printed number minus PDF page) that fits an edge line on at least
       half of the pages. Without such an offset, nothing is removed.
    2. A "stem" is a line without that number. A stem that recurs is a running footer.
    3. Remove, at page edges only: a running stem with the page number, with a Roman
       number, or alone; and a line that is only the page number.
    A numbered clause or heading stays, because its text appears once."""
    edges = [_edge_lines(page.text) for page in pages]
    readings = [{i: _readings(line, page.page) for i, line in edge.items()}
                for page, edge in zip(pages, edges)]

    pages_with_offset: dict[int, set[int]] = defaultdict(set)
    for n, per_line in enumerate(readings):
        for options in per_line.values():
            for _, offset in options:
                pages_with_offset[offset].add(n)
    enough = max(MIN_RUNNING_PAGES, len(pages) / 2)
    numbering = {offset for offset, found in pages_with_offset.items() if len(found) >= enough}
    if not numbering:
        return [set() for _ in pages]

    pages_with_stem: dict[str, set[int]] = defaultdict(set)
    for n, per_line in enumerate(readings):
        for options in per_line.values():
            for stem, offset in options:
                if stem and offset in numbering:
                    pages_with_stem[stem].add(n)
    running = {stem for stem, found in pages_with_stem.items()
               if len(found) >= MIN_RUNNING_PAGES}

    def is_footer(line: str, options: list[tuple[str, int]]) -> bool:
        if any(offset in numbering and (stem == "" or stem in running)
               for stem, offset in options):
            return True
        tokens = line.split()
        if _squash(tokens) in running:                      # the title without a number
            return True
        return any(_ROMAN.fullmatch(token) and _squash(tokens[:i] + tokens[i + 1:]) in running
                   for i, token in enumerate(tokens))

    return [{i for i, line in edge.items() if is_footer(line, readings[n][i])}
            for n, edge in enumerate(edges)]


def _edge_lines(text: str) -> dict[int, str]:
    """The first and last non-empty lines of a page, by line number."""
    filled = [(i, line) for i, line in enumerate(text.splitlines()) if line.strip()]
    return dict(filled[:EDGE_LINES] + filled[-EDGE_LINES:])


def _readings(line: str, page: int) -> list[tuple[str, int]]:
    """Every way to read the line as "stem plus page number": (stem, number - page)."""
    tokens = line.split()
    if all(_NUMBER.fullmatch(token) for token in tokens):
        return [("", int("".join(tokens)) - page)]      # kerning can print 10 as "1 0"
    return [(_squash(tokens[:i] + tokens[i + 1:]), int(token) - page)
            for i, token in enumerate(tokens) if _NUMBER.fullmatch(token)]


def _squash(tokens: list[str]) -> str:
    """Without any space, so a word split by kerning ("U ndergraduate") still matches."""
    return "".join(tokens).lower()


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
