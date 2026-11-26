"""Check the golden questions against the documents themselves.

For every answerable question in golden.jsonl and golden_candidates.jsonl this
re-extracts each cited page and confirms that the evidence phrases listed in
golden_evidence.md are on it. For every unanswerable question it re-runs the
listed search patterns over all extractable text and expects no match.

It does not use the application's extractor, chunker or retriever. It cannot
read image-only pages, and it does not judge whether an answer is correct: that
is the reviewer's job.

Run from anywhere:  python eval/check_golden_pages.py   (exit code 1 on failure)
Needs corpus/raw/ (python corpus/fetch.py) and the built Batch B files.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pdfplumber

EVAL = Path(__file__).resolve().parent
RAW = EVAL.parent / "corpus" / "raw"
BATCH_B = EVAL / "batch_b" / "docs"

# (doc_id, doc_version) -> file
DOCUMENTS = {
    ("ug-academic-rules", "2023-08"): RAW / "undergraduate-academic-rules-aug-2023.pdf",
    ("ug-academic-rules", "2026-06"): RAW / "academic-rules-handbook-jun-2026.pdf",
    ("prospectus-academic-rules", "2026"):
        RAW / "prospectus-2026-academic-rules-and-regulations.pdf",
    ("student-handbook", "2026-06"): RAW / "student-handbook-jun-2026.pdf",
    ("annual-report-2019-20", "2019-20"): RAW / "annual-report-2019-20.pdf",
    ("annual-report-2023-24", "2023-24"): RAW / "annual-report-2023-24.pdf",
    ("fee-structure", "2026-27"): RAW / "fee-structure-page.html",
    ("grading", "2026"): RAW / "grading-page.html",
    ("faculty-entrepreneurship-policy", "2022-05"):
        RAW / "faculty-entrepreneurship-and-technical-start-ups-policy.pdf",
    ("fee-structure", "2027-28"): BATCH_B / "fee-structure-2027-28.html",
    ("student-id-card-replacement", "2026-09"): BATCH_B / "id-card-replacement-procedure.md",
    ("industrial-internship-guidelines", "2026-08"):
        BATCH_B / "industrial-internship-guidelines.md",
    ("makerspace-equipment-loan-rules", "2026-09"):
        BATCH_B / "makerspace-equipment-loans.html",
    ("student-cloud-credits", "2026-10"): BATCH_B / "student-cloud-credits.md",
    ("student-locker-allocation", "2026-07"): BATCH_B / "student-locker-allocation.md",
}
# A Batch B file that no question cites, still searched for the unanswerable ones.
SEARCH_ONLY = (BATCH_B / "notice-copy.md",)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def squash(text: str) -> str:
    return " ".join(text.replace("|", " ").split())


def visible_text(page) -> str:
    """Text inside the page area. Some PDFs keep a neighbouring page's text outside it."""

    def inside(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        x = (obj["x0"] + obj["x1"]) / 2
        y = (obj["top"] + obj["bottom"]) / 2
        return 0 <= x <= page.width and 0 <= y <= page.height

    return page.filter(inside).extract_text() or ""


def pages_of(path: Path) -> list[str]:
    if path.suffix == ".pdf":
        with pdfplumber.open(path) as pdf:
            return [squash(visible_text(page)) for page in pdf.pages]
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".html":
        parser = _Text()
        parser.feed(text)
        text = "".join(parser.parts)
    return [squash(text)]


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_evidence(path: Path) -> tuple[dict, dict]:
    """Return {(id, doc_id, version, page): [phrases]} and {id: [patterns]}."""
    phrases: dict = {}
    patterns: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if not cells or not re.fullmatch(r"[gc]\d{3}", cells[0]):
            continue
        if len(cells) == 5:
            key = (cells[0], cells[1], cells[2], int(cells[3]))
            phrases.setdefault(key, []).append(cells[4].strip("`"))
        else:
            patterns.setdefault(cells[0], []).append(cells[1].strip("`").replace("\\|", "|"))
    return phrases, patterns


def main() -> int:
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    absent = [str(p) for p in (*DOCUMENTS.values(), *SEARCH_ONLY) if not p.is_file()]
    if absent:
        print("Cannot check, files are missing:", *absent, sep="\n  ")
        return 1
    pages = {key: pages_of(path) for key, path in DOCUMENTS.items()}
    everything = [(path.name, n, text)
                  for key, path in DOCUMENTS.items()
                  for n, text in enumerate(pages[key], 1)]
    everything += [(path.name, 1, pages_of(path)[0]) for path in SEARCH_ONLY]
    phrases, patterns = read_evidence(EVAL / "golden_evidence.md")

    problems: list[str] = []
    questions = rows(EVAL / "golden.jsonl") + rows(EVAL / "golden_candidates.jsonl")
    for field in ("id", "question"):
        values = [q[field].lower() for q in questions]
        if len(values) != len(set(values)):
            problems.append(f"repeated {field} across the two question files")
    used: set = set()
    cited_pages = checked_phrases = checked_patterns = 0
    for q in questions:
        if q["category"] == "unanswerable":
            if q["relevant"] or not patterns.get(q["id"]):
                problems.append(f"{q['id']}: needs search patterns and no ground truth")
            for pattern in patterns.get(q["id"], []):
                checked_patterns += 1
                hits = [f"{name} p{n}" for name, n, text in everything
                        if re.search(pattern, text, re.I)]
                if hits:
                    problems.append(f"{q['id']}: {pattern!r} matches {hits[:5]}")
            continue
        if not q["relevant"] or not q["expected_answer"]:
            problems.append(f"{q['id']}: needs ground truth and an expected answer")
        for item in q["relevant"]:
            cited_pages += 1
            doc = (item["doc_id"], item["doc_version"])
            key = (q["id"], *doc, item["page"])
            used.add(key)
            if doc not in pages or not 1 <= item["page"] <= len(pages[doc]):
                problems.append(f"{q['id']}: unknown document or page {doc} {item['page']}")
                continue
            if not phrases.get(key):
                problems.append(f"{q['id']}: no evidence phrase for {doc} page {item['page']}")
            for phrase in phrases.get(key, []):
                checked_phrases += 1
                if len(phrase.split()) >= 15:
                    problems.append(f"{q['id']}: phrase has 15 or more words")
                if squash(phrase).lower() not in pages[doc][item["page"] - 1].lower():
                    problems.append(f"{q['id']}: not on {doc} page {item['page']}: {phrase!r}")
    problems += [f"evidence row without matching ground truth: {key}"
                 for key in phrases if key not in used]

    for problem in problems:
        print("FAIL", problem)
    print(f"{len(questions)} questions, {cited_pages} cited pages, "
          f"{checked_phrases} phrases, {checked_patterns} absence patterns, "
          f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
