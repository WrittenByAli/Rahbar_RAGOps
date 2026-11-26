"""Heading lines are source text, so they must reach the index (ADR 0006).

The heading detector often takes the first line of a numbered clause for a heading,
so a heading line can hold the very fact a student asks about.
"""

from __future__ import annotations

from collections import Counter

import pytest

from pipeline.chunk import chunk
from pipeline.clean import clean
from pipeline.models import Document, Page

CLAUSE = "1.21 Students are required to maintain 100% attendance in"


def make_doc(*page_texts: str) -> Document:
    """One Page per argument, numbered from 1."""
    pages = [Page(source="policy.pdf", page=n, text=text)
             for n, text in enumerate(page_texts, start=1)]
    return Document(source="policy.pdf", doc_version="2026-06", superseded=False, pages=pages)


# A running header and footer on all four pages, plus every awkward heading position:
# text before the first heading, headings with nothing under them, a clause line taken
# for a heading, a heading at the foot of a page, and headings at the end of the file.
HEADER = "Example Institute Rulebook"
FOOTER = "Internal circulation copy"
PAGE_BODIES = [
    "opening words before any heading\n## Scope\nthese rules apply to everyone",
    f"CHAPTER TWO\nATTENDANCE AND LEAVE\n{CLAUSE}\nevery course of the semester\n2.2 Short Leave",
    "leave is granted by the head\n\nrequests must be written\n## Appendix Forms",
    "FINAL NOTES SECTION",
]


def awkward_doc() -> Document:
    return make_doc(*(f"{HEADER}\n{body}\n{FOOTER}" for body in PAGE_BODIES))


def source_words() -> list[str]:
    """Every word of the document except the running header and footer. The Markdown
    "##" marks are markup, not words of the document."""
    return [word for body in PAGE_BODIES for word in body.split() if word != "##"]


@pytest.mark.parametrize("size", [1, 4, 9, 400])
def test_every_source_word_reaches_a_chunk_exactly_once_without_overlap(size: int) -> None:
    chunks = chunk(clean(awkward_doc()), size=size, overlap=0)

    # Same words in the same order: nothing lost, repeated or invented ("Introduction").
    assert [word for c in chunks for word in c.text.split()] == source_words()
    assert all(len(c.text.split()) <= size for c in chunks)


@pytest.mark.parametrize("size, overlap", [(4, 2), (9, 5), (400, 50)])
def test_every_source_word_reaches_a_chunk_with_overlap(size: int, overlap: int) -> None:
    chunks = chunk(clean(awkward_doc()), size=size, overlap=overlap)

    indexed = Counter(word for c in chunks for word in c.text.split())
    assert not Counter(source_words()) - indexed
    assert set(indexed) == set(source_words())
    assert all(len(c.text.split()) <= size + overlap for c in chunks)


def test_section_labels_are_the_detected_headings() -> None:
    chunks = chunk(clean(awkward_doc()), size=400, overlap=50)

    assert [(c.section, c.page_start, c.page_end) for c in chunks] == [
        ("Introduction", 1, 1),
        ("Scope", 1, 1),
        (CLAUSE, 2, 2),             # the two chapter headings are in front of it
        ("2.2 Short Leave", 2, 3),  # heading on page 2, its text on page 3
        ("FINAL NOTES SECTION", 3, 4),
    ]


def test_heading_line_that_holds_a_fact_is_in_the_chunk_text() -> None:
    [only] = chunk(make_doc(f"{CLAUSE}\nevery course"), size=400, overlap=50)

    assert only.section == CLAUSE
    assert only.text == f"{CLAUSE}\nevery course"


def test_heading_opens_only_the_first_chunk_of_its_section() -> None:
    chunks = chunk(make_doc("## Rules\na0 a1 a2 a3 a4 a5 a6 a7 a8"), size=4, overlap=0)

    assert [c.text for c in chunks] == ["Rules\na0 a1 a2", "a3 a4 a5 a6", "a7 a8"]
    assert {c.section for c in chunks} == {"Rules"}


@pytest.mark.parametrize("gap", ["\n", "\n\n"])
def test_heading_is_not_left_alone_when_the_first_paragraph_is_long(gap: str) -> None:
    # Markdown puts an empty line under a heading. The heading must still share a chunk
    # with the start of its text, even when the two together do not fit.
    doc = make_doc("## Rules" + gap + "a0 a1 a2 a3 a4 a5")

    chunks = chunk(doc, size=6, overlap=0)

    assert [c.text for c in chunks] == ["Rules\na0 a1 a2 a3 a4", "a5"]


def test_heading_with_nothing_under_it_goes_in_front_of_the_next_chunk() -> None:
    doc = make_doc("## Part One\n## Attendance\nbe there")

    chunks = chunk(doc, size=400, overlap=50)

    assert [(c.section, c.text) for c in chunks] == [
        ("Attendance", "Part One\nAttendance\nbe there"),
    ]


def test_headings_at_the_end_of_a_document_get_their_own_chunk() -> None:
    doc = make_doc("## One\nsome text\n## Annex\n## Forms")

    chunks = chunk(doc, size=400, overlap=50)

    assert [(c.section, c.text) for c in chunks] == [
        ("One", "One\nsome text"),
        ("Forms", "Annex\nForms"),
    ]


def test_introduction_label_is_never_added_to_the_text() -> None:
    [without_heading] = chunk(make_doc("words with no heading"), size=400, overlap=50)
    [real_heading] = chunk(make_doc("## Introduction\nreal text"), size=400, overlap=50)

    assert (without_heading.section, without_heading.text) == (
        "Introduction", "words with no heading")
    assert real_heading.text == "Introduction\nreal text"


def test_empty_heading_on_an_earlier_page_widens_the_range() -> None:
    doc = make_doc("PART ONE HERE", "## Rules\nbody text")

    [only] = chunk(doc, size=400, overlap=50)

    assert (only.section, only.page, only.page_start, only.page_end) == ("Rules", 1, 1, 2)
