"""Running headers and footers are removed; text that appears once is not."""

from __future__ import annotations

from pipeline.clean import clean
from pipeline.models import Document, Page


def _document(*pages: list[str]) -> Document:
    return Document(
        source="short.pdf", doc_version="2026", superseded=False,
        pages=[Page(source="short.pdf", page=i, text="\n".join(lines))
               for i, lines in enumerate(pages, 1)],
    )


def test_two_page_document_keeps_lines_that_appear_once() -> None:
    # With two pages, "half of the pages" is one page, so every edge line used to qualify.
    first = [f"first page line {i}" for i in range(8)]
    second = [f"second page line {i}" for i in range(8)]
    text = "\n".join(p.text for p in clean(_document(first, second)).pages)
    assert [line for line in first + second if line not in text] == []


def test_two_page_document_still_loses_a_header_that_is_on_both_pages() -> None:
    header = "NATIONAL UNIVERSITY RUNNING HEADER"
    pages = clean(_document([header, "alpha", "beta", "gamma", "delta"],
                            [header, "epsilon", "zeta", "eta", "theta"])).pages
    assert all(header not in page.text for page in pages)
    assert "alpha" in pages[0].text and "theta" in pages[1].text


def test_a_line_repeated_on_one_page_is_not_a_running_header() -> None:
    # "Header" means it appears on many PAGES. Counting occurrences deleted this answer.
    first = ["Yes", "Yes", "alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    second = ["one", "two", "three", "Is a medical certificate needed?", "Yes", "four",
              "five", "six"]
    pages = clean(_document(first, second)).pages
    assert pages[0].text.splitlines().count("Yes") == 2
    assert "Yes" in pages[1].text.splitlines()
