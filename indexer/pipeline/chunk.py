"""Stage 3: Chunk. Structure-aware, with overlap."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import NamedTuple

from .models import Chunk, Document

# Bump this whenever a change here can alter a chunk's text, section, pages or id. It belongs
# in the re-embedding fingerprint (ARCHITECTURE 3.6). "1" was the Assignment 02 chunker.
CHUNKER_VERSION = "3"


class Word(NamedTuple):
    """One word of the document. The page is kept per word because one paragraph can run
    over a page break."""

    text: str
    page: int                   # the page it is printed on
    end: str = " "              # what follows it in chunk text: "\n" closes a heading line


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Split on headings, then pack whole paragraphs into chunks of at most `size` words.
    A paragraph longer than `size` is cut by word count. Up to `overlap` words are then
    repeated from the previous chunk (see _overlap_words).

    Heading lines are part of the text and count towards `size` like any other words.
    page_start and page_end are the pages of the words in the chunk's text."""
    chunks: list[Chunk] = []
    chunks_under_heading: Counter[str] = Counter()

    for section, paragraphs in _split_into_sections(doc):
        previous: list[Word] = []       # starts empty, so overlap never crosses a heading
        for own_words in _pack_paragraphs(paragraphs, size):
            first_page = own_words[0].page
            last_page = own_words[-1].page
            repeated = _overlap_words(previous, first_page, overlap)
            text = _join(repeated + own_words)
            ordinal = chunks_under_heading[section]
            chunks_under_heading[section] += 1
            chunks.append(Chunk(
                id=_chunk_id(doc, section, ordinal, text),
                text=text,
                doc_id=doc.doc_id,
                source=doc.source,
                section=section,
                page_start=first_page,
                page_end=last_page,
                doc_version=doc.doc_version,
                superseded=doc.superseded,
            ))
            previous = own_words

    return chunks


def _split_into_sections(doc: Document) -> list[tuple[str, list[list[Word]]]]:
    """Return (label, paragraphs) for each section to be chunked.

    A heading line opens the first paragraph of its section, so packing cannot leave it
    apart from the start of its text. A heading with no text under it waits, and goes in
    front of the next section that has text. If none follows, the waiting headings form a
    section of their own, labelled with the last one. "Introduction" is only a label for
    text before the first heading; it is not added to the text."""
    sections: list[tuple[str, list[list[Word]]]] = []
    label = "Introduction"
    headings: list[Word] = []           # heading lines not yet placed in a section
    body: list[list[Word]] = [[]]       # paragraphs under the current heading

    for page in doc.pages:
        for line in page.text.splitlines():
            next_label = _extract_heading(line)
            if next_label:
                if any(body):
                    sections.append((label, _open_with(headings, body)))
                    headings = []
                label, body = next_label, [[]]
                headings += _heading_line(next_label, page.page)
            elif line:
                body[-1].extend(Word(word, page.page) for word in line.split())
            else:
                body.append([])         # an empty line ends a paragraph; a page break does not

    if any(body):
        sections.append((label, _open_with(headings, body)))
    elif headings:
        sections.append((label, [headings]))
    return sections


def _heading_line(heading: str, page: int) -> list[Word]:
    """The heading's words (without Markdown "#" marks), ending in a line break."""
    words = [Word(word, page) for word in heading.split()]
    words[-1] = words[-1]._replace(end="\n")
    return words


def _open_with(headings: list[Word], body: list[list[Word]]) -> list[list[Word]]:
    """Drop empty paragraphs and put the heading lines at the start of the first one."""
    first, *rest = [paragraph for paragraph in body if paragraph]
    return [headings + first, *rest]


_NUMBERED_CLAUSE = re.compile(r"^(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z].{2,78})$")


def _looks_like_table_row(stripped: str) -> bool:
    """Reject ALL-CAPS lines that are table rows or datelines, not real
    headings: checkbox columns ("X X X X"), single-letter column codes
    ("A B C"), or lines that are mostly digits/punctuation."""
    tokens = stripped.split()
    single_char_tokens = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    if single_char_tokens >= 2:
        return True
    digit_chars = sum(1 for ch in stripped if ch.isdigit())
    return digit_chars / max(len(stripped), 1) > 0.3


def _extract_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
    if match:
        return match.group(2).strip()
    # Numbered clauses, e.g. "1.21 Attendance": the real heading style FAST-NUCES
    # PDFs use, which the ALL-CAPS check below never catches.
    clause = _NUMBERED_CLAUSE.match(stripped)
    if clause and not _looks_like_table_row(stripped):
        return stripped
    if (
        len(stripped) < 80
        and stripped == stripped.upper()
        and len(stripped.split()) >= 2
        and not stripped.startswith("|")
        and re.search(r"[A-Z]", stripped)
        and not _looks_like_table_row(stripped)
    ):
        return stripped
    return None


def _pack_paragraphs(paragraphs: list[list[Word]], size: int) -> list[list[Word]]:
    """Group one section's words into chunks, before any overlap is added."""
    groups: list[list[Word]] = []
    buffer: list[Word] = []
    for words in paragraphs:
        if len(buffer) + len(words) <= size:
            buffer.extend(words)
            continue
        if buffer:
            groups.append(buffer)
        if len(words) <= size:
            buffer = list(words)
        else:
            groups.extend(words[i:i + size] for i in range(0, len(words), size))
            buffer = []
    if buffer:
        groups.append(buffer)
    return groups


def _overlap_words(previous: list[Word], first_page: int, overlap: int) -> list[Word]:
    """The end of the previous chunk, to repeat in front of the next one.

    Only words from the page where the next chunk starts are taken. So overlap never moves
    page_start back: a citation shows the page where the chunk's own text begins, and every
    word in the chunk is still inside [page_start, page_end]."""
    if overlap <= 0:                    # previous[-0:] would be the whole list
        return []
    return [word for word in previous[-overlap:] if word.page == first_page]


def _join(words: list[Word]) -> str:
    return "".join(word.text + word.end for word in words).rstrip()


def _chunk_id(doc: Document, section: str, ordinal: int, text: str) -> str:
    """Hash of doc_id, version, section, ordinal and a hash of the text (ARCHITECTURE 3.6).

    `ordinal` counts the chunks under one heading name, so a section that gains a chunk does
    not renumber other sections. Page numbers and `superseded` are not part of the id."""
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    key = "\n".join([doc.doc_id, doc.doc_version, section, str(ordinal), text_hash])
    return hashlib.sha256(key.encode()).hexdigest()[:16]
