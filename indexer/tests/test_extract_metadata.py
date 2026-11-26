"""Extraction fixes and the metadata contract (D-04, ARCHITECTURE 3.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fakes import make_pdf

from pipeline import extract as extract_module
from pipeline.extract import extract
from pipeline.metadata import derive_doc_id, normalise_title, resolve, version_key

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- extraction
def test_text_outside_the_visible_page_is_not_extracted(tmp_path: Path) -> None:
    """Both handbooks are spreads: each PDF page shows one half, but the file holds both.
    Reading both merges the two columns line by line and puts text on the wrong page."""
    pdf = make_pdf(tmp_path / "spread.pdf",
                   [[(20, 500, "left page words"), (450, 500, "right page words")],
                    [(-380, 500, "left page words"), (20, 500, "right page words")]])

    pages = extract(str(pdf), "2026", False).pages

    assert [p.text for p in pages] == ["left page words", "right page words"]


@pytest.mark.parametrize("media, crop", [
    ((100, 50, 500, 650), None),                    # the page does not start at 0, 0
    ((-200, -100, 200, 500), None),
    ((0, 0, 900, 600), (0, 0, 400, 600)),           # a CropBox shows less than the sheet
    ((100, 50, 900, 650), (500, 50, 900, 650)),     # the right half, with a moved origin
    ((0, 0, 400, 900), (0, 300, 400, 900)),         # the upper part
])
def test_only_the_visible_box_is_read_whatever_its_origin(tmp_path: Path, media, crop) -> None:
    x0, y0, x1, y1 = crop or media
    inside = [(x0 + 20, y1 - 30, "first visible line"), (x0 + 20, y0 + 30, "last visible line")]
    outside = [(x1 + 40, y1 - 30, "right of the page"), (x0 - 390, y1 - 60, "left of the page"),
               (x0 + 20, y0 - 40, "below the page"), (x0 + 20, y1 + 40, "above the page")]
    pdf = make_pdf(tmp_path / "boxes.pdf", [inside + outside], media_box=media, crop_box=crop)

    [page] = extract(str(pdf), "2026", False).pages

    assert page.text.splitlines() == ["first visible line", "last visible line"]


def test_ocr_failure_is_recorded_not_raised(tmp_path: Path, monkeypatch) -> None:
    def missing(path: str, page: int) -> str:
        raise FileNotFoundError("tesseract is not installed")

    monkeypatch.setattr(extract_module, "_ocr_page", missing)
    pdf = make_pdf(tmp_path / "scan.pdf", [[(20, 500, "cover")], []])

    pages = extract(str(pdf), "2026", False).pages

    assert pages[0].ocr is False and pages[0].ocr_error == ""
    assert pages[1].ocr is True and pages[1].text == ""
    assert "tesseract is not installed" in pages[1].ocr_error


def test_markdown_front_matter_is_not_body_text(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("---\ndoc_id: x\ntitle: \"A: B\"\n---\n# Heading\n\nBody.\n",
                    encoding="utf-8")

    [page] = extract(str(path), "2026", False).pages

    assert page.text.strip() == "# Heading\n\nBody."


def test_markdown_that_only_looks_like_front_matter_keeps_its_text(tmp_path: Path) -> None:
    path = tmp_path / "rule.md"
    path.write_text("---\nA horizontal rule opens this file.\n", encoding="utf-8")

    [page] = extract(str(path), "2026", False).pages

    assert "horizontal rule" in page.text


# ---------------------------------------------------------------- versions
def test_versions_sort_by_date_not_by_text() -> None:
    ordered = ["2019-20", "2022-05", "2023", "2023-08", "2023-08-30", "2023-24",
               "2026-06", "2026-27"]
    assert all(version_key(v) is not None for v in ordered)
    assert version_key("2023-08") < version_key("2023-08-30") < version_key("2026-06")
    assert version_key("2019-20") < version_key("2023-24") < version_key("2026-27")
    assert version_key("2023") < version_key("2023-08")


@pytest.mark.parametrize("bad", ["", "v2", "Fall 2026", "2026-13", "2026-02-30", "2023-26",
                                 "26-06", "2026/06"])
def test_other_version_formats_are_refused(bad: str) -> None:
    assert version_key(bad) is None


# ---------------------------------------------------------------- resolution
def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_sidecar_wins_over_front_matter_and_each_source_is_recorded(tmp_path: Path) -> None:
    doc = write(tmp_path / "a.md", "---\ntitle: From front matter\nsource: front\n"
                                   "doc_version: 2024\n---\nbody\n")
    write(tmp_path / "a.md.meta.yaml", "doc_id: rules\ntitle: From the sidecar\n"
                                       "doc_version: 2026-06-15\npage_count: 3\n")

    meta = resolve(doc, raw_sha256="ab" * 32)

    assert (meta.doc_id, meta.title, meta.source, meta.doc_version) == (
        "rules", "From the sidecar", "front", "2026-06-15")
    assert meta.declared_page_count == 3
    assert meta.sources == {"doc_id": "sidecar", "title": "sidecar", "source": "front_matter",
                            "doc_version": "sidecar"}
    assert meta.problems == []


def test_yaml_numbers_become_strings(tmp_path: Path) -> None:
    doc = write(tmp_path / "a.md", "---\ndoc_version: 2023\ntitle: T\n---\nbody\n")

    assert resolve(doc, raw_sha256="ab" * 32).doc_version == "2023"


def test_embedded_metadata_fills_only_what_is_missing(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "b.pdf", [[(20, 500, "text")]],
                   info={"Title": "Leave Policy", "CreationDate": "D:20260620074917+05'00'"})
    html = write(tmp_path / "c.html",
                 "<html><head><title> Fee &amp; Dues </title></head><body>x</body></html>")

    from_pdf = resolve(pdf, raw_sha256="cd" * 32)
    from_html = resolve(html, raw_sha256="ef" * 32)

    assert (from_pdf.title, from_pdf.doc_version, from_pdf.source) == (
        "Leave Policy", "2026-06-20", "")
    assert from_pdf.sources["title"] == from_pdf.sources["doc_version"] == "pdf_info"
    assert (from_html.title, from_html.doc_version, from_html.source) == (
        "Fee & Dues", "", "")
    assert from_html.sources["title"] == "html_title"
    assert "source" not in from_pdf.sources                  # never invented


@pytest.mark.parametrize("junk", ["untitled", "Microsoft Word - Report-2019-20",
                                  "final layout.cdr", "   "])
def test_placeholder_titles_count_as_missing(tmp_path: Path, junk: str) -> None:
    pdf = make_pdf(tmp_path / "d.pdf", [[(20, 500, "text")]], info={"Title": junk})

    assert resolve(pdf, raw_sha256="cd" * 32).title == ""


def test_title_is_never_taken_from_a_heading(tmp_path: Path) -> None:
    doc = write(tmp_path / "e.md", "# A Very Obvious Title\n\nbody\n")

    meta = resolve(doc, raw_sha256="cd" * 32)

    assert (meta.title, meta.source, meta.doc_version) == ("", "", "")


def test_doc_id_without_a_sidecar_is_deterministic(tmp_path: Path) -> None:
    first = resolve(write(tmp_path / "Leave Policy (final).md", "body"), raw_sha256="12" * 32)

    assert first.doc_id == "leave-policy-final-12121212" == derive_doc_id(
        "Leave Policy (final).md", "12" * 32)
    assert first.sources["doc_id"] == "derived"
    assert derive_doc_id("Leave Policy (final).md", "34" * 32) != first.doc_id


@pytest.mark.parametrize("value", ["../escape", "Has Spaces", "a/b", ".hidden"])
def test_a_doc_id_that_is_not_a_safe_folder_name_is_a_problem(tmp_path: Path,
                                                              value: str) -> None:
    doc = write(tmp_path / "a.md", "body")
    write(tmp_path / "a.md.meta.yaml", f"doc_id: '{value}'\n")

    meta = resolve(doc, raw_sha256="12" * 32)

    assert meta.sources["doc_id"] == "derived"
    assert any("doc_id" in problem for problem in meta.problems)


@pytest.mark.parametrize("text", ["title: [unclosed", "- just\n- a list\n",
                                  "title:\n  nested: true\n"])
def test_a_broken_sidecar_is_reported(tmp_path: Path, text: str) -> None:
    doc = write(tmp_path / "a.md", "body")
    write(tmp_path / "a.md.meta.yaml", text)

    assert any("sidecar" in problem for problem in resolve(doc, raw_sha256="12" * 32).problems)


def test_titles_are_compared_without_years_or_punctuation() -> None:
    assert normalise_title("Leave  Policy, 2026!") == normalise_title("leave policy")
    assert normalise_title("Annual Report 2019-20") == normalise_title("Annual Report 2023-24")
    assert normalise_title("Leave Policy") != normalise_title("Travel Policy")


# ---------------------------------------------------------------- corpus A sidecars
CORPUS_A = {
    "undergraduate-academic-rules-aug-2023.pdf": ("ug-academic-rules", "2023-08"),
    "academic-rules-handbook-jun-2026.pdf": ("ug-academic-rules", "2026-06"),
    "prospectus-2026-academic-rules-and-regulations.pdf": ("prospectus-academic-rules", "2026"),
    "student-handbook-jun-2026.pdf": ("student-handbook", "2026-06"),
    "annual-report-2019-20.pdf": ("annual-report-2019-20", "2019-20"),
    "annual-report-2023-24.pdf": ("annual-report-2023-24", "2023-24"),
    "fee-structure-page.html": ("fee-structure", "2026-27"),
    "grading-page.html": ("grading", "2026"),
    "faculty-entrepreneurship-and-technical-start-ups-policy.pdf":
        ("faculty-entrepreneurship-policy", "2022-05"),
}


@pytest.mark.parametrize("name", sorted(CORPUS_A))
def test_corpus_a_sidecar_matches_the_agreed_identity(name: str) -> None:
    data = yaml.safe_load(
        (REPO / "corpus" / "sidecars" / f"{name}.meta.yaml").read_text("utf-8"))

    assert (str(data["doc_id"]), str(data["doc_version"])) == CORPUS_A[name]
    assert version_key(str(data["doc_version"])) is not None
    sources = yaml.safe_load((REPO / "corpus" / "sources.yaml").read_text("utf-8"))["documents"]
    listed = {str(d["name"]): d["url"] for d in sources}
    assert data["title"] in listed
    assert data["source"].replace("%20", " ") == listed[data["title"]].replace("%20", " ")
    assert set(data) == {"doc_id", "title", "source", "doc_version"}


def test_there_is_one_sidecar_for_each_corpus_a_document() -> None:
    found = {p.name.removesuffix(".meta.yaml")
             for p in (REPO / "corpus" / "sidecars").glob("*.meta.yaml")}

    assert found == set(CORPUS_A)
