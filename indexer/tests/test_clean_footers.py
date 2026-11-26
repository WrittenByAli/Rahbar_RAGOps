"""Running footers whose page number changes (ADR 0006, "not fixed").

The line shapes copy what extraction produces for the real handbooks: left and right
pages put the number on different sides, front matter uses Roman numerals, kerning can
split a word ("U ndergraduate") or a number ("1 0"), and some pages print the number on
its own line above the title.
"""

from __future__ import annotations

from pipeline.chunk import chunk
from pipeline.clean import CLEANER_VERSION, clean
from pipeline.models import Document, Page

CLAUSE = "1.21 Students are required to maintain 100% attendance in"
TOPICS = ["admission", "attendance", "grading", "warning", "probation", "repeats",
          "transfer", "graduation", "medals", "conduct", "retakes", "refunds"]


def body(page: int) -> list[str]:
    topic = TOPICS[page % len(TOPICS)]
    return [f"This page explains the {topic} rules.",
            f"Every student reads the {topic} section with care.",
            f"Questions about {topic} go to the office.",
            f"Nothing else about {topic} is said here."]


def document(pages: list[list[str]]) -> Document:
    return Document(source="book.pdf", doc_version="2026-06", superseded=False,
                    pages=[Page(source="book.pdf", page=n, text="\n".join(lines))
                           for n, lines in enumerate(pages, 1)])


def lines_of(doc: Document) -> list[str]:
    return [line for page in doc.pages for line in page.text.splitlines() if line]


def alternating_footer(page: int) -> str:
    """Printed numbers start on PDF page 9; the layout swaps sides."""
    printed = page - 8
    return f"{printed} Student Handbook 2026" if printed % 2 == 0 else (
        f"Student Handbook 2026 {printed}")


ROMAN = {4: "II Student Handbook 2026", 5: "Student Handbook 2026 III",
         8: "VI Student Handbook 2026"}


def handbook(extra: dict[int, list[str]] | None = None) -> Document:
    pages = []
    for page in range(1, 31):
        lines = list((extra or {}).get(page, [])) + body(page)
        if page >= 9:
            lines.append(alternating_footer(page))
        elif page in ROMAN:
            lines.append(ROMAN[page])
        pages.append(lines)
    return document(pages)


def test_footer_with_a_changing_page_number_is_removed() -> None:
    cleaned = lines_of(clean(handbook()))

    assert [line for line in cleaned if "Student Handbook" in line] == []
    assert len(cleaned) == 30 * 4                       # nothing else went


def test_footer_with_split_words_and_a_separate_number_line_is_removed() -> None:
    pages = []
    for page in range(1, 41):
        lines = body(page)
        printed = page - 6
        if printed > 0 and printed % 2:                 # odd pages: one line, split word
            lines.append(f"U ndergraduate Academic Rules 2026 {printed}")
        elif printed > 0:                               # even pages: number, then title
            number = str(printed) if printed < 10 else " ".join(str(printed))
            lines += [number, "Undergraduate Academic Rules 2026"]
        pages.append(lines)

    cleaned = lines_of(clean(document(pages)))

    assert [line for line in cleaned if "ndergraduate" in line] == []
    assert [line for line in cleaned if line.replace(" ", "").isdigit()] == []
    assert len(cleaned) == 40 * 4


def test_page_n_of_m_and_bare_numbers_are_removed() -> None:
    counted = document([body(n) + [f"Page {n} of 12"] for n in range(1, 13)])
    bare = document([body(n) + [str(n + 216)] for n in range(1, 6)])

    assert not [x for x in lines_of(clean(counted)) if x.startswith("Page")]
    assert not [x for x in lines_of(clean(bare)) if x.isdigit()]


def test_numbered_clause_headings_are_kept() -> None:
    # Clause lines at the very top of pages, where the footer search looks.
    clauses = {10: [CLAUSE], 11: ["1.22 Late arrival counts as absence"],
               12: ["12 Weeks Of Teaching Form One Term"],     # a number that fits page-8 no
               13: ["5 Academic Assessment"]}                  # fits 13-8, but appears once
    cleaned = lines_of(clean(handbook(clauses)))

    for [line] in clauses.values():
        assert line in cleaned, line


def test_numbered_headings_that_follow_the_page_but_differ_are_kept() -> None:
    # "1 Introduction" on page 1, "2 Admission" on page 2...: the numbers track the pages,
    # but the lines are not one repeated line.
    titles = ["Introduction", "Admission", "Attendance", "Grading", "Warning", "Probation"]
    doc = document([[f"{n} {title}", *body(n)] for n, title in enumerate(titles, 1)])

    cleaned = lines_of(clean(doc))

    assert [f"{n} {t}" for n, t in enumerate(titles, 1) if f"{n} {t}" not in cleaned] == []


def test_table_rows_and_repeated_answers_are_kept() -> None:
    pages = []
    for page in range(1, 13):
        pages.append([f"| {page} | Course fee | 100 |", *body(page), "Yes",
                      f"Total credit hours {130 + page % 3}", f"{page - 8 + 40}\tseats",
                      f"{page} Student Handbook 2026"])

    cleaned = lines_of(clean(document(pages)))

    assert len([x for x in cleaned if x.startswith("| ")]) == 12
    assert len([x for x in cleaned if x.startswith("Total credit hours")]) == 12
    assert len([x for x in cleaned if x.endswith("seats")]) == 12
    assert not [x for x in cleaned if x.endswith("Student Handbook 2026")]


def test_numbers_that_do_not_follow_the_page_are_kept() -> None:
    doc = document([body(n) + [f"Room {7 * n % 11} Block"] for n in range(1, 13)])

    assert len([x for x in lines_of(clean(doc)) if x.startswith("Room")]) == 12


def test_a_footer_line_in_the_middle_of_a_page_is_kept() -> None:
    mention = "12 Student Handbook 2026"
    doc = handbook({20: [*body(0), mention, *body(1)]})

    assert mention in lines_of(clean(doc))


def test_two_numbered_pages_are_not_enough_to_call_something_a_footer() -> None:
    doc = document([["Part 1", *body(1)], ["Part 2", *body(2)]])

    assert {"Part 1", "Part 2"} <= set(lines_of(clean(doc)))


def test_footer_no_longer_shifts_page_start() -> None:
    # "4 Student Handbook 2026" closes PDF page 12 and used to open a section there, so
    # text on page 13 was cited as page 12.
    doc = handbook({13: ["## Social Media"]})

    chunks = chunk(clean(doc), size=400, overlap=50)

    social = [c for c in chunks if c.section == "Social Media"]
    assert social and social[0].page_start == 13
    assert not [c for c in chunks if "Student Handbook" in c.section]


def test_cleaner_version_is_declared() -> None:
    assert isinstance(CLEANER_VERSION, str) and CLEANER_VERSION
