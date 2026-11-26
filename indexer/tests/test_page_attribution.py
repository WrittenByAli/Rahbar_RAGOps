"""Every chunk reports the pages its own text came from (P.4).

The golden set is scored by page: a chunk matches when the ground-truth page lies
in [page_start, page_end]. A wrong range here would silently change recall.
"""

from __future__ import annotations

import pytest

from pipeline.chunk import chunk
from pipeline.models import Chunk, Document, Page


def make_doc(*page_texts: str) -> Document:
    """One Page per argument, numbered from 1."""
    pages = [Page(source="policy.pdf", page=n, text=text)
             for n, text in enumerate(page_texts, start=1)]
    return Document(source="policy.pdf", doc_version="2026-06", superseded=False, pages=pages)


def words(prefix: str, count: int) -> str:
    """words("a", 3) is "a0 a1 a2"."""
    return " ".join(f"{prefix}{i}" for i in range(count))


def page_ranges(chunks: list[Chunk]) -> list[tuple[int, int]]:
    return [(c.page_start, c.page_end) for c in chunks]


def test_chunk_on_later_page_reports_that_page() -> None:
    # One section: the heading is on page 1, the body runs on to pages 2 and 3.
    # The heading counts as a word, so each page holds exactly 10.
    doc = make_doc("## Rules\n" + words("a", 9), words("b", 10), words("c", 10))

    chunks = chunk(doc, size=10, overlap=0)

    assert [c.text for c in chunks] == ["Rules\n" + words("a", 9), words("b", 10),
                                        words("c", 10)]
    assert [c.page for c in chunks] == [1, 2, 3]
    assert page_ranges(chunks) == [(1, 1), (2, 2), (3, 3)]


def test_chunk_spanning_pages_reports_range() -> None:
    doc = make_doc("## Cover\nfront matter",
                   "## Rules\n" + words("a", 4),
                   words("b", 4))

    rules = chunk(doc, size=400, overlap=0)[1]

    assert rules.text == f"Rules\n{words('a', 4)} {words('b', 4)}"
    assert (rules.page_start, rules.page_end) == (2, 3)
    assert rules.page == 2


def test_paragraphs_packed_from_two_pages_report_the_range() -> None:
    doc = make_doc("## Rules\nfirst paragraph\n\n", "second paragraph")

    chunks = chunk(doc, size=400, overlap=0)

    assert [c.text for c in chunks] == ["Rules\nfirst paragraph second paragraph"]
    assert page_ranges(chunks) == [(1, 2)]


def test_pieces_of_a_long_paragraph_report_their_own_pages() -> None:
    doc = make_doc("## Rules\n" + words("a", 14), words("b", 15))

    chunks = chunk(doc, size=10, overlap=0)

    assert page_ranges(chunks) == [(1, 1), (1, 2), (2, 2)]


def test_text_before_first_heading_reports_its_own_pages() -> None:
    doc = make_doc("preamble starts here", "preamble ends here\n## Rules\nbody text")

    intro, rules = chunk(doc, size=400, overlap=0)

    assert intro.section == "Introduction"
    assert (intro.page_start, intro.page_end) == (1, 2)
    assert (rules.page_start, rules.page_end) == (2, 2)


def test_heading_at_foot_of_page_is_inside_the_range() -> None:
    # The heading line is part of the chunk text, so its page is part of the range.
    doc = make_doc("## Cover\nfront text\n## Rules", "body text")

    rules = chunk(doc, size=400, overlap=0)[1]

    assert rules.text == "Rules\nbody text"
    assert (rules.page_start, rules.page_end) == (1, 2)
    assert rules.page == 1


def test_document_made_only_of_headings_is_still_indexed_with_its_pages() -> None:
    doc = make_doc("FIRST BIG BANNER", "SECOND BIG BANNER")

    chunks = chunk(doc, size=400, overlap=0)

    assert [c.text for c in chunks] == ["FIRST BIG BANNER\nSECOND BIG BANNER"]
    assert page_ranges(chunks) == [(1, 2)]


def test_document_without_text_gives_no_chunks() -> None:
    assert chunk(make_doc("", ""), size=400, overlap=50) == []


# --- the overlap rule ---------------------------------------------------------

def test_overlap_repeats_the_previous_words_from_the_same_page() -> None:
    doc = make_doc("## Rules\n" + words("a", 11))

    chunks = chunk(doc, size=6, overlap=2)

    assert [c.text for c in chunks] == ["Rules\na0 a1 a2 a3 a4", "a3 a4 a5 a6 a7 a8 a9 a10"]


def test_overlap_does_not_cross_a_section_boundary() -> None:
    doc = make_doc("## One\n" + words("a", 11) + "\n## Two\n" + words("b", 11))

    chunks = chunk(doc, size=6, overlap=3)

    assert [(c.section, c.text) for c in chunks] == [
        ("One", "One\na0 a1 a2 a3 a4"),
        ("One", "a2 a3 a4 a5 a6 a7 a8 a9 a10"),
        ("Two", "Two\nb0 b1 b2 b3 b4"),
        ("Two", "b2 b3 b4 b5 b6 b7 b8 b9 b10"),
    ]


def test_overlap_does_not_cross_between_sections_with_the_same_heading() -> None:
    doc = make_doc("## Notes\nfirst note text\n## Notes\nsecond note text")

    chunks = chunk(doc, size=400, overlap=50)

    assert [c.text for c in chunks] == ["Notes\nfirst note text", "Notes\nsecond note text"]


def test_overlap_stops_at_a_page_break() -> None:
    doc = make_doc("## Rules\n" + words("a", 5), words("b", 6))

    chunks = chunk(doc, size=6, overlap=3)

    assert [c.text for c in chunks] == ["Rules\n" + words("a", 5), words("b", 6)]
    assert page_ranges(chunks) == [(1, 1), (2, 2)]


def test_overlap_takes_only_words_on_the_page_where_the_new_chunk_starts() -> None:
    # The first piece is Rules a0 a1 a2 b0 b1; the second starts at b2, on page 2. Of
    # the three overlap words (a2 b0 b1), only the two from page 2 may be repeated.
    doc = make_doc("## Rules\n" + words("a", 3), words("b", 8))

    chunks = chunk(doc, size=6, overlap=3)

    assert [c.text for c in chunks] == ["Rules\na0 a1 a2 b0 b1", words("b", 8)]
    assert page_ranges(chunks) == [(1, 2), (2, 2)]


@pytest.mark.parametrize("size, overlap", [(1, 0), (3, 0), (5, 2), (7, 7), (10, 4), (400, 50)])
def test_page_range_matches_every_word_in_the_chunk(size: int, overlap: int) -> None:
    # Every word, heading words included, is tagged with its page ("p2w5"), so the
    # right range can be read back from the chunk text, overlap words included.
    def tagged(page: int, first: int, last: int) -> str:
        return " ".join(f"p{page}w{i}" for i in range(first, last))

    doc = make_doc(
        tagged(1, 0, 4) + "\n## " + tagged(1, 20, 22) + "\n" + tagged(1, 4, 13)
        + "\n## " + tagged(1, 22, 23),                      # its text is on page 2
        tagged(2, 0, 3) + "\n\n" + tagged(2, 3, 11)
        + "\n## " + tagged(2, 20, 22) + "\n## " + tagged(2, 22, 24),
        tagged(3, 0, 9) + "\n## " + tagged(3, 20, 22),      # nothing until page 4
        "## " + tagged(4, 20, 21) + "\n" + tagged(4, 0, 2) + "\n\n" + tagged(4, 2, 17)
        + "\n## " + tagged(4, 21, 23),                      # nothing follows
    )

    chunks = chunk(doc, size=size, overlap=overlap)

    seen: set[str] = set()
    for c in chunks:
        pages = [int(word[1:word.index("w")]) for word in c.text.split()]
        assert (c.page_start, c.page_end) == (min(pages), max(pages)), c.text
        assert len(pages) <= size + overlap
        seen.update(c.text.split())
    body = {w for page in doc.pages for w in page.text.split() if w.startswith("p")}
    assert seen == body
