"""Chunk ids and chunk metadata (ARCHITECTURE 3.6).

Incremental ingestion compares ids to decide what to embed, so an id must change
when the text changes and must never be shared by two versions of a document.
"""

from __future__ import annotations

import hashlib

from pipeline.chunk import chunk
from pipeline.clean import clean
from pipeline.models import Document, Page

TEXT = "## Attendance\nstudents attend classes\n## Grading\ngrades are relative"


def make_doc(text: str = TEXT, version: str = "2026-06", source: str = "rules.pdf",
             superseded: bool = False, **extra: str) -> Document:
    return Document(source=source, doc_version=version, superseded=superseded,
                    pages=[Page(source=source, page=1, text=text)], **extra)


def ids(doc: Document, size: int = 400, overlap: int = 0) -> list[str]:
    return [c.id for c in chunk(doc, size=size, overlap=overlap)]


def test_two_versions_of_a_document_never_share_a_chunk_id() -> None:
    # Same file name and same text: only the version differs.
    old = ids(make_doc(version="2023-08"))
    new = ids(make_doc(version="2026-06"))

    assert len(old) == len(new) == 2
    assert not set(old) & set(new)


def test_different_documents_never_share_a_chunk_id() -> None:
    first = ids(make_doc(doc_id="ug-rules"))
    second = ids(make_doc(doc_id="student-handbook"))

    assert not set(first) & set(second)


def test_identical_text_within_one_document_gets_distinct_ids() -> None:
    repeated_heading = chunk(make_doc("## Notes\nsame text\n## Notes\nsame text"),
                             size=400, overlap=0)
    repeated_paragraph = chunk(make_doc("## Notes\nfiller\n\nsame text\n\nsame text"),
                               size=2, overlap=0)

    assert repeated_heading[0].text == repeated_heading[1].text
    assert repeated_heading[0].id != repeated_heading[1].id
    assert [c.text for c in repeated_paragraph[1:]] == ["same text", "same text"]
    assert len({c.id for c in repeated_paragraph}) == 3


def test_edited_text_gets_a_new_id() -> None:
    before = ids(make_doc("## Attendance\nstudents attend 75 percent"))
    after = ids(make_doc("## Attendance\nstudents attend 80 percent"))

    assert before != after


def test_edit_that_adds_a_chunk_keeps_ids_in_later_sections() -> None:
    before = chunk(make_doc("## One\na b c\n## Two\nd e f"), size=3, overlap=0)
    after = chunk(make_doc("## One\na b c g h i\n## Two\nd e f"), size=3, overlap=0)

    assert len(after) == len(before) + 1
    assert after[0].id == before[0].id
    assert after[-1].id == before[-1].id


def test_ids_follow_doc_id_not_the_file_name() -> None:
    first = ids(make_doc(source="rules.pdf", doc_id="ug-rules"))
    renamed = ids(make_doc(source="rules (1).pdf", doc_id="ug-rules"))

    assert first == renamed


def test_superseded_flag_does_not_change_ids() -> None:
    # Archive and restore only update metadata; nothing is embedded again.
    assert ids(make_doc(superseded=False)) == ids(make_doc(superseded=True))


def test_chunk_id_is_built_from_the_documented_fields() -> None:
    doc = make_doc("## Rules\nbody text", doc_id="ug-rules")
    text_hash = hashlib.sha256(b"Rules\nbody text").hexdigest()
    key = "\n".join(["ug-rules", "2026-06", "Rules", "0", text_hash])

    assert ids(doc) == [hashlib.sha256(key.encode()).hexdigest()[:16]]


def test_doc_id_falls_back_to_the_source_file_name() -> None:
    doc = make_doc(source="handbook.pdf")

    assert doc.doc_id == "handbook.pdf"
    assert {c.doc_id for c in chunk(doc, size=400, overlap=0)} == {"handbook.pdf"}


def test_doc_id_survives_cleaning() -> None:
    doc = make_doc(doc_id="ug-academic-rules")

    cleaned = clean(doc)

    assert cleaned.doc_id == "ug-academic-rules"
    assert {c.doc_id for c in chunk(cleaned, size=400, overlap=0)} == {"ug-academic-rules"}


def test_chunk_metadata_has_doc_id_and_page_range() -> None:
    doc = Document(source="rules.pdf", doc_version="2026-06", superseded=True,
                   doc_id="ug-rules",
                   pages=[Page(source="rules.pdf", page=7, text="## Rules\nfirst words"),
                          Page(source="rules.pdf", page=8, text="last words")])

    [only] = chunk(doc, size=400, overlap=0)

    assert only.metadata() == {
        "doc_id": "ug-rules",
        "source": "rules.pdf",
        "section": "Rules",
        "page": 7,
        "page_start": 7,
        "page_end": 8,
        "doc_version": "2026-06",
        "superseded": True,
    }


def test_chunker_version_is_declared() -> None:
    from pipeline.chunk import CHUNKER_VERSION

    assert isinstance(CHUNKER_VERSION, str) and CHUNKER_VERSION
