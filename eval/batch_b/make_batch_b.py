"""Build the Batch B documents that are derived from corpus A.

Reads corpus/raw/ (third-party files, never committed) and writes into
eval/batch_b/docs/. Every file written here, sidecars included, is listed in
eval/batch_b/.gitignore. The self-authored Batch B documents are ordinary
committed files and this script does not touch them.

The same input bytes always give the same output bytes: no clock, no random
numbers, and files are written in binary mode so Windows adds no CR characters.

Run:  python eval/batch_b/make_batch_b.py
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = REPO / "corpus" / "raw"
CORPUS_MANIFEST = REPO / "corpus" / "MANIFEST.md"
DOCS = HERE / "docs"

ANNUAL_REPORT = "annual-report-2019-20.pdf"
PROSPECTUS = "prospectus-2026-academic-rules-and-regulations.pdf"
RULES_HANDBOOK = "academic-rules-handbook-jun-2026.pdf"
GRADING_PAGE = "grading-page.html"
FEE_PAGE = "fee-structure-page.html"
SOURCES = (ANNUAL_REPORT, PROSPECTUS, RULES_HANDBOOK, GRADING_PAGE, FEE_PAGE)

# The same wording goes into every sidecar and every altered file, so the marker
# says nothing about which problem a particular file carries.
SOURCE_LABEL = "Batch B coursework test data"
MARKER = (
    "Coursework test data. This file was altered for testing and is not an "
    "official FAST-NUCES publication."
)
MARKER_HTML = f"<p><strong>{MARKER}</strong></p>"


# ---------------------------------------------------------------- helpers
def check_sources() -> None:
    """Stop before writing anything if a source is missing. Warn if one differs."""
    missing = [name for name in SOURCES if not (RAW / name).is_file()]
    if missing:
        sys.exit(
            f"ERROR: corpus A files are missing from {RAW}: {', '.join(missing)}\n"
            "Download them first:  python corpus/fetch.py"
        )
    # Manifest rows look like: | name | version | .. | `file` | bytes | `sha256` | <url> |
    recorded = dict(
        re.findall(
            r"\| `([^`]+)` \| [\d,]+ \| `([0-9a-f]{64})` \|",
            CORPUS_MANIFEST.read_text(encoding="utf-8"),
        )
    )
    for name in SOURCES:
        actual = hashlib.sha256((RAW / name).read_bytes()).hexdigest()
        if recorded.get(name) != actual:
            print(
                f"WARNING: {name} differs from corpus/MANIFEST.md, so the output "
                "will differ from a machine that has the recorded bytes."
            )


def must_match_once(count: int, wanted: str) -> None:
    if count != 1:
        sys.exit(
            f"ERROR: expected exactly one match for {wanted!r}, found {count}. "
            "The source page has changed; update this script."
        )


def sub_once(text: str, pattern: str, replacement: str) -> str:
    """Apply a regular expression that must match exactly once."""
    result, count = re.subn(pattern, replacement, text)
    must_match_once(count, pattern)
    return result


def replace_once(text: str, old: str, new: str) -> str:
    """Replace a literal string that must occur exactly once."""
    must_match_once(text.count(old), old)
    return text.replace(old, new)


def require_in(text: str, phrases: tuple[str, ...], name: str) -> None:
    absent = [phrase for phrase in phrases if phrase not in text]
    if absent:
        sys.exit(f"ERROR: {name} no longer contains: {absent}. Update this script.")


def read_html(name: str) -> str:
    # Plain "utf-8" keeps the byte order mark and the CRLF line endings as they are.
    return (RAW / name).read_bytes().decode("utf-8")


def page_count(name: str) -> int:
    return len(PdfReader(str(RAW / name)).pages)


def write_document(file_name: str, data: bytes, **metadata: object) -> None:
    """Write one document and its metadata sidecar."""
    (DOCS / file_name).write_bytes(data)
    sidecar = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    (DOCS / f"{file_name}.meta.yaml").write_bytes(sidecar.encode("utf-8"))
    print(f"wrote docs/{file_name} ({len(data):,} bytes) + sidecar")


# ---------------------------------------------------------------- documents
def make_byte_copy() -> None:
    """The 2019-20 annual report, uploaded again as if it were the next year's."""
    write_document(
        "annual-report-2020-21.pdf",
        (RAW / ANNUAL_REPORT).read_bytes(),
        doc_id="annual-report-2020-21",
        title="Annual Report 2020-21",
        source=SOURCE_LABEL,
        doc_version="2020-21",
        page_count=page_count(ANNUAL_REPORT),
    )


def make_resaved_pdf() -> None:
    """The prospectus rules passed through another PDF tool: new bytes, same text.

    No visible marker is added, because any added text would break the property.
    The marker is in the PDF's own metadata instead.
    """
    writer = PdfWriter()
    for page in PdfReader(str(RAW / PROSPECTUS)).pages:
        writer.add_page(page)
    writer.add_metadata(
        {
            "/Title": "Academic Regulations Booklet",
            "/Subject": "Coursework test data, not an official FAST-NUCES publication",
        }
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    write_document(
        "academic-regulations-booklet.pdf",
        buffer.getvalue(),
        doc_id="academic-regulations-booklet",
        title="Academic Regulations Booklet",
        source=SOURCE_LABEL,
        doc_version="2026-08",
        page_count=page_count(PROSPECTUS),
    )


def make_truncated_pdf() -> None:
    """A "revised" rules handbook whose download stopped after 60% of the bytes.

    It claims to be a newer version of the current handbook. If it were accepted,
    it would replace a good document with a broken one. A cut binary file cannot
    carry a visible marker; the sidecar's source field is the marker.
    """
    data = (RAW / RULES_HANDBOOK).read_bytes()
    write_document(
        "academic-rules-handbook-revised.pdf",
        data[: len(data) * 6 // 10],
        doc_id="ug-academic-rules",
        title="Academic Rules Handbook (Revised November 2026)",
        source=SOURCE_LABEL,
        doc_version="2026-11",
        page_count=page_count(RULES_HANDBOOK),
    )


# Small wording changes. No grade, number or rule changes. Each search string is
# kept as short as it can be, so little of the source page is quoted here.
GRADING_WORDING_EDITS = (
    ("<title>Academic Grading</title>", "<title>Grading Guide for Students</title>"),
    (
        '<h2 class="captions">Academic Grading</h2>',
        '<h2 class="captions">Grading Guide for Students</h2>',
    ),
    ("The performance of students is", "Student performance is"),
    ("mid term tests", "mid-term tests"),
    ("method considered appropriate by", "method judged suitable by"),
    ("University, This shall be", "University. This will be"),
    (
        "For almost all courses, the semester work will contribute",
        "In almost all courses, semester work contributes",
    ),
    ("issued by the University will be final", "awarded by the University is final"),
    ("The following points need to be noted", "Please note the following points"),
    ("Repeat courses will be indicated", "Repeated courses are shown"),
    ("with a warning cannot register in a subsequent", "on warning may not register in a later"),
    ("If Warning Count becomes three", "When the Warning Count reaches three"),
    ("admission of student at the University", "student's admission to the University"),
    (
        "for genuine reasons subject to the following",
        "on genuine grounds, subject to the conditions below",
    ),
    ("Faill (Attendance", "Fail (Attendance"),
)


def make_edited_grading_page() -> None:
    """The grading page re-issued under a new name with small wording edits."""
    text = read_html(GRADING_PAGE)
    for old, new in GRADING_WORDING_EDITS:
        text = replace_once(text, old, new)
    anchor = '<div class="alert alert-info">'
    text = replace_once(text, anchor, MARKER_HTML + "\r\n        " + anchor)
    write_document(
        "grading-guide-for-students.html",
        text.encode("utf-8"),
        doc_id="student-grading-guide",
        title="Grading Guide for Students",
        source=SOURCE_LABEL,
        doc_version="2026-09",
    )


# (pattern, replacement) pairs. Table cells are matched together with their row
# label, because several rows hold the same amount.
FEE_CHANGES = (
    (r"w\.e\.f\. Fall 2026 semester", "w.e.f. Fall 2027 semester"),
    (r"For the academic year 2026-27", "For the academic year 2027-28"),
    (r"Admission Fee of Rs 30,000", "Admission Fee of Rs 35,000"),
    (r'(<td>BBA / BS</td>\s*<td class="text-center">)Rs\. 12,000', r"\g<1>Rs. 13,200"),
    (r'(<td>MBA / MS</td>\s*<td class="text-center">)Rs\. 12,000', r"\g<1>Rs. 13,800"),
    (r"Students contribute Rs 2,500", "Students contribute Rs 3,000"),
    (r"(Paper Rechecking Fee \(Per Course/Lab\)</td>\s*<td[^>]*>)Rs\. 500", r"\g<1>Rs. 750"),
)


def make_newer_fee_page() -> None:
    """Next year's fee page: a few amounts change, everything else stays."""
    text = read_html(FEE_PAGE)
    for pattern, replacement in FEE_CHANGES:
        text = sub_once(text, pattern, replacement)
    anchor = r'(<br />\s*<div class="section">\s*<div class="container">)'
    text = sub_once(text, anchor, r"\g<1>" + "\r\n        " + MARKER_HTML)
    write_document(
        "fee-structure-2027-28.html",
        text.encode("utf-8"),
        doc_id="fee-structure",
        title="Fee Structure 2027-28",
        source=SOURCE_LABEL,
        doc_version="2027-28",
    )


# Sentences the rewritten guide below depends on. If the source page changes,
# the guide may no longer state the same facts, so the script stops.
GRADING_FACTS = (
    "the final examination will contribute the remaining 50% of the total marks",
    "only the most recent grade points are used",
    "is awarded for PhD Thesis work only",
    "If Warning Count becomes three",
    "the Warning Count becomes zero",
    "MS students under warning (CGPA less than 2.50)",
    "satisfactorily met all other requirements for the course except the final examination",
    "converted to appropriate grade before the end of next semester",
    "completion of maximum duration allowed to earn a degree",
)

# Written from scratch for this assignment. It states the same facts as the
# grading page in different words, order and layout. Deliberately hard to catch.
PARAPHRASED_GUIDE = f"""\
# How Your Grades Work: A Plain-Language Guide

{MARKER}

## Where your marks come from

In nearly every course, half of your result is earned while the semester is
running and the other half in the final exam. Coursework can include quizzes,
assignments, midterms and case studies, and your teacher and the University may
add other kinds of assessment. You should be told how you will be assessed when
the course begins. Your letter grade reflects how you did across the whole
course, and whatever grade the University issues is the last word.

## The two averages

The semester average (SGPA) covers one semester only. Each course counts in
proportion to its credit hours out of all the credit hours you took that term.
The cumulative average (CGPA) covers everything you have studied up to now. If
you retake a course, only your latest attempt counts, and this is true even when
the new result is worse than the old one. Your transcript marks retaken courses
and shows how many times each one has been repeated.

## What the letters are worth

For bachelor's students (BS and BBA), both A+ and A earn 4.00 points and are
rated excellent. A- earns 3.67 and B+ earns 3.33, both rated very good. B is
worth 3.00 (good), B- 2.67 (average), C+ 2.33 (below average) and C 2.00
(adequate). C- at 1.67, D+ at 1.33 and D at 1.00 are all counted as passes.

Master's students (MS and MBA) see the same points from A+ down to C, and A+ and
A are still excellent, but the other descriptions are stricter: A- is very good,
B+ is good, B is average, B- is below average, C+ is adequate and C is only a
pass.

Doctoral students have a shorter scale that stops at B- (2.67, below average).
A+ and A give 4.00 (excellent), A- gives 3.67 (very good), B+ gives 3.33 (good)
and B gives 3.00 (average).

Some codes apply in every programme. F means you failed, and FA means you failed
because your attendance was too low; both carry zero points. CR is used while
research is still continuing, I means the course is incomplete, and W means you
withdrew. CR, I and W are left out when either average is worked out. A W stays
visible on your transcript like other grades. S and U are used for doctoral
thesis work alone and never affect the CGPA.

## Academic warning

Your CGPA is checked when each semester finishes, and your very first semester
and summer terms are checked too. To stay clear of a warning you need at least
2.0 in a bachelor's programme, 2.5 in a master's programme or 3.0 in a PhD. Each
time you finish below your line, you receive a warning and your warning count
goes up by one. While you are on warning, you need permission from your Head of
Department, or from a senior teacher the Head has chosen, before you can enrol
again. Reaching the line or going above it resets your count to nothing. A third
count ends your admission. Admission also ends by itself once you have used up
the longest period allowed for finishing your degree.

## Retaking courses

You may retake a course you have not passed, or one where you want a better
grade. Anything you failed has to be taken again. If you are on warning, you
must also retake passed courses whose grade points fall below the CGPA your
programme requires. A master's student on warning, with a CGPA under 2.50, would
need to retake courses scored below 2.50, which the rule illustrates as grades
of C or C-.

## When you cannot sit the final

If you have a real reason, you can ask for more time to take the final exam. You
will receive an I only if everything else in the course, apart from the final,
has been completed to a satisfactory standard. The I is a placeholder that is
later replaced by a proper grade. This must happen before the next semester is
over; otherwise the I turns into an F on its own.
"""


def make_paraphrased_grading_guide() -> None:
    require_in(read_html(GRADING_PAGE), GRADING_FACTS, GRADING_PAGE)
    write_document(
        "how-your-grades-work.md",
        PARAPHRASED_GUIDE.encode("utf-8"),
        doc_id="plain-language-grades-guide",
        title="How Your Grades Work: A Plain-Language Guide",
        source=SOURCE_LABEL,
        doc_version="2026-09",
    )


def main() -> None:
    check_sources()
    DOCS.mkdir(exist_ok=True)
    make_byte_copy()
    make_resaved_pdf()
    make_truncated_pdf()
    make_edited_grading_page()
    make_newer_fee_page()
    make_paraphrased_grading_guide()


if __name__ == "__main__":
    main()
