"""Stage 3: Verify. Four checks on every document, before anything is chunked (A1).

The functions here change nothing. They get the candidates, what the data lake already
holds, and a source of window vectors, and they return one verdict per candidate.

Each candidate is classified in the order of ARCHITECTURE 3.4. Two steps of that order
matter most:
- "unchanged" is tested before "duplicate", or a second run would reject the whole corpus
  as copies of itself;
- the document family (doc_id) is resolved before the near-duplicate test, or every new
  version would be rejected as a near-copy of the one before it.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import thresholds as limits
from .metadata import Metadata, normalise_title, version_key, version_order
from .models import Page
from .neardup import WindowVectors, best_similarities, share_at_least

CHECKS = ("quality", "completeness", "recency", "uniqueness")
SUPPORTED = (".pdf", ".html", ".htm", ".md", ".txt")


@dataclass(frozen=True)
class CheckResult:
    status: str                      # "pass" or "fail"
    reason: str
    score: float | None = None       # similarity, for the uniqueness check

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"status": self.status, "reason": self.reason}
        if self.score is not None:
            result["score"] = round(self.score, 4)
        return result


def passed(reason: str, score: float | None = None) -> CheckResult:
    return CheckResult("pass", reason, score)


def failed(reason: str, score: float | None = None) -> CheckResult:
    return CheckResult("fail", reason, score)


@dataclass
class Candidate:
    """A file waiting to be verified, with everything the checks look at."""

    file: str
    batch: str
    path: Path
    raw_sha256: str
    size_bytes: int
    meta: Metadata
    pages: list[Page]                # as extracted; empty if the file could not be read
    text: str                        # cleaned and normalised; hashes and windows use it
    content_sha256: str              # "" when there is no text
    file_problem: str = ""           # why the file is unreadable
    truncation: list[str] = field(default_factory=list)
    read_only: bool = False          # from CORPUS_DIR or --source, not the inbox


@dataclass(frozen=True)
class Known:
    """A document version that candidates are compared with."""

    doc_id: str
    version: str
    file: str
    title: str
    raw_sha256: str
    content_sha256: str
    status: str                      # "active" or "superseded"
    text: str

    @property
    def name(self) -> str:
        return f"{self.doc_id} {self.version} ({self.file})"


@dataclass
class Verdict:
    candidate: Candidate
    checks: dict[str, CheckResult]
    decision: str                    # "accepted", "unchanged" or "quarantined"
    action: str = ""                 # new, newer, changed, changed_archived,
                                     # unchanged or ignored

    @property
    def failed_checks(self) -> list[str]:
        return [name for name in CHECKS if self.checks[name].status == "fail"]

    def entry(self) -> dict[str, object]:
        """This document's row in the verification report."""
        c = self.candidate
        return {
            "file": c.file, "batch": c.batch, "doc_id": c.meta.doc_id,
            "doc_version": c.meta.doc_version, "title": c.meta.title,
            "source": c.meta.source, "decision": self.decision, "action": self.action,
            "metadata_source": dict(c.meta.sources), "raw_sha256": c.raw_sha256,
            "content_sha256": c.content_sha256,
            "checks": {name: self.checks[name].as_dict() for name in CHECKS},
        }


# ---------------------------------------------------------------- text and file facts
def normalised_text(pages: list[Page]) -> str:
    """All words of the cleaned pages, one space apart, without Markdown heading marks.
    The same text gives the same string from a PDF, a web page or a Markdown file."""
    return " ".join(word for page in pages for line in page.text.splitlines()
                    for word in line.lstrip("#").split())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""


def magic_problem(suffix: str, data: bytes) -> str:
    """Why these bytes cannot be a file of this type, or "" if they can."""
    if suffix not in SUPPORTED:
        return f"unsupported file type '{suffix or 'no extension'}'"
    if not data:
        return "the file is empty (0 bytes)"
    if suffix == ".pdf":
        return "" if b"%PDF-" in data[:1024] else "does not start like a PDF (no %PDF header)"
    if data.startswith((b"%PDF-", b"PK\x03\x04")) or b"\x00" in data[:4096]:
        return f"is a binary file, not {suffix} text"
    if suffix in (".md", ".txt"):          # web pages may declare other encodings
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return f"is not valid UTF-8 text (first bad byte at position {exc.start})"
    if suffix in (".html", ".htm") and not data.removeprefix(b"\xef\xbb\xbf").lstrip(
            ).startswith(b"<"):
        return "does not start like an HTML page"
    return ""


def truncation_signals(suffix: str, data: bytes) -> list[str]:
    """Signs that the end of the file is missing or the file is damaged."""
    if suffix == ".pdf":
        signals = []
        if b"%%EOF" not in data[-1024:]:
            signals.append("no %%EOF marker at the end of the PDF")
        problem = _strict_pdf_problem(data)
        if problem:
            signals.append(f"fails strict PDF parsing ({problem})")
        return signals
    if suffix in (".html", ".htm") and b"</html>" not in data.lower():
        return ["the HTML has no closing </html> tag"]
    return []


def _strict_pdf_problem(data: bytes) -> str:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    try:
        for page in PdfReader(io.BytesIO(data), strict=True).pages:
            page.get_contents()
    except Exception as exc:  # noqa: BLE001 - any parser error means the file is damaged
        return f"{type(exc).__name__}: {str(exc)[:80]}"
    return ""


# ---------------------------------------------------------------- quality
_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)")     # PDF text with no usable character
_NUMBER_TOKEN = re.compile(r"\d+([.,:/-]\d+)*(st|nd|rd|th)?%?")
_PUNCTUATION = ".,;:!?()[]{}\"'‘’“”•-–—/\\|*%#&+=<>$"


def _is_wordlike(token: str) -> bool:
    core = token.strip(_PUNCTUATION)
    if not core:
        return False
    if _NUMBER_TOKEN.fullmatch(core):
        return True
    return sum(ch.isalpha() for ch in core) / len(core) >= 0.7


def check_quality(candidate: Candidate) -> CheckResult:
    """Empty, garbled or failed extraction (A1.3)."""
    if candidate.file_problem:
        return failed(candidate.file_problem)
    pages = candidate.pages
    ocr_note = _ocr_note(pages)
    if not candidate.text:
        return failed("no text was extracted" + ocr_note)

    raw = "\n".join(page.text for page in pages)
    visible = [ch for ch in raw if not ch.isspace()]
    per_page = len(visible) / len(pages)
    empty = sum(1 for p in pages
                if len("".join(p.text.split())) < limits.EMPTY_PAGE_CHARS)
    unreadable = (sum(1 for ch in visible if ch == "�" or not ch.isprintable())
                  + sum(len(found) for found in _UNMAPPED_GLYPH.findall(raw)))
    tokens = raw.split()
    wordlike = sum(_is_wordlike(t) for t in tokens) / max(len(tokens), 1)

    problems = []
    if empty / len(pages) > limits.MAX_EMPTY_PAGE_SHARE:
        problems.append(f"{empty} of {len(pages)} pages have no text")
    if per_page < limits.MIN_CHARS_PER_PAGE:
        problems.append(f"only {per_page:.0f} characters per page "
                        f"(minimum {limits.MIN_CHARS_PER_PAGE})")
    share = unreadable / max(len(visible), 1)
    if share > limits.MAX_UNREADABLE_CHAR_SHARE:
        problems.append(f"{share:.0%} unreadable characters (replacement, unprintable or "
                        "unmapped glyphs)")
    if wordlike < limits.MIN_WORDLIKE_SHARE:
        problems.append(f"only {wordlike:.0%} of tokens look like words or numbers "
                        f"(minimum {limits.MIN_WORDLIKE_SHARE:.0%})")
    if problems:
        return failed("; ".join(problems) + ocr_note)
    return passed(f"{per_page:.0f} characters per page, {wordlike:.0%} word-like tokens, "
                  f"{empty} of {len(pages)} pages without text" + ocr_note)


def _ocr_note(pages: list[Page]) -> str:
    errors = [p for p in pages if p.ocr_error]
    if errors:
        return (f"; OCR failed on {len(errors)} page(s), which are not indexed "
                f"({errors[0].ocr_error[:160]})")
    silent = sum(1 for p in pages if p.ocr and not p.text.strip())
    return f"; OCR returned nothing on {silent} page(s)" if silent else ""


# ---------------------------------------------------------------- completeness
def check_completeness(candidate: Candidate) -> CheckResult:
    """Source, title, date or version and page count present; not truncated (A1.4)."""
    meta, pages = candidate.meta, len(candidate.pages)
    missing = [name for name in ("source", "title", "doc_version") if not getattr(meta, name)]
    if not pages:
        missing.append("page count (the file could not be read)")

    problems = [f"missing: {', '.join(missing)}"] if missing else []
    if meta.doc_version and version_key(meta.doc_version) is None:
        problems.append(f"doc_version '{meta.doc_version}' is not YYYY, YYYY-MM, YYYY-MM-DD "
                        "or an academic year YYYY-YY")
    problems += meta.problems

    truncation = list(candidate.truncation)
    if meta.declared_page_count is not None and pages and meta.declared_page_count != pages:
        truncation.append(f"the metadata declares {meta.declared_page_count} pages, "
                          f"the file has {pages}")
    if meta.declared_size_bytes is not None and meta.declared_size_bytes != candidate.size_bytes:
        truncation.append(f"the metadata declares {meta.declared_size_bytes} bytes, "
                          f"the file has {candidate.size_bytes}")
    if meta.declared_sha256 and meta.declared_sha256 != candidate.raw_sha256:
        truncation.append("the declared sha256 differs from the file's")
    if truncation:
        problems.append(f"truncated or incomplete: {'; '.join(truncation)}")

    if problems:
        return failed("; ".join(problems))
    return passed(f"source, title, doc_version and page count ({pages}) present; "
                  "no sign of truncation")


# ---------------------------------------------------------------- recency
def check_recency(candidate: Candidate, family: list[Known],
                  pinned: bool) -> tuple[CheckResult, str]:
    """Compare with the other versions of the same doc_id (A1.5). Returns the verdict
    and what accepting the file would mean."""
    doc_id, version = candidate.meta.doc_id, candidate.meta.doc_version
    if version_key(version) is None:
        return failed(f"cannot compare: no usable doc_version ('{version}')"), ""
    if not family:
        return passed(f"first version of {doc_id} in the data lake"), "new"

    active = next(k for k in family if k.status == "active")
    if version == active.version:
        return passed(f"same version as the active one ({version}) with different text: "
                      "a corrected copy"), "changed"
    if pinned and any(k.version == version for k in family):
        return passed(f"corrected copy of the archived version {version}; version "
                      f"{active.version} was restored, so it stays the active one"
                      ), "changed_archived"
    if version_order(version) < version_order(active.version):
        return failed(f"stale: version {version} is older than the active version "
                      f"{active.version} of {doc_id}"), ""
    return passed(f"newer than the active version {active.version}, which will be archived "
                  "as superseded"), "newer"


# ---------------------------------------------------------------- uniqueness
def find_exact_duplicate(candidate: Candidate, known: list[Known],
                         earlier: list[Candidate]) -> str:
    """The reason, if the file repeats another document or an earlier file of this batch.

    `earlier` holds only files that passed. A copy that was rejected (say, for missing
    metadata) must not get the complete original rejected as well."""
    def how(raw: str, content: str) -> str:
        if raw == candidate.raw_sha256:
            return "same raw bytes"
        if content and content == candidate.content_sha256:
            return "same text after extraction and cleaning"
        return ""

    for first in earlier:
        same = how(first.raw_sha256, first.content_sha256)
        if same:
            return f"exact copy of {first.file}, an earlier file in this batch: {same}"
    for other in known:
        same = how(other.raw_sha256, other.content_sha256)
        if same and other.doc_id != candidate.meta.doc_id:
            return f"exact copy of {other.name}: {same}"
    return ""


def check_near_duplicate(candidate: Candidate, others: list[Known],
                         vectors: WindowVectors) -> CheckResult:
    """The share of the candidate's windows that have a match in another document."""
    others = [k for k in others if k.text]
    if not others:
        return passed("no other document in the data lake to compare with", 0.0)

    mine = vectors.of(candidate.content_sha256, candidate.text)
    shares = [(share_at_least(best_similarities(mine, vectors.of(k.content_sha256, k.text)),
                              limits.NEAR_DUP_SIMILARITY), k) for k in others]
    share, closest = max(shares, key=lambda pair: pair[0])
    measure = (f"{share:.0%} of its text windows have a match at cosine "
               f"{limits.NEAR_DUP_SIMILARITY} or higher (near-duplicate from "
               f"{limits.NEAR_DUP_SHARE:.0%})")
    if share >= limits.NEAR_DUP_SHARE:
        result = failed(f"near-duplicate of {closest.name}: {measure}", share)
    else:
        result = passed(f"closest other document is {closest.name}: {measure}", share)
    return _with_suggestion(result, candidate, shares)


def _with_suggestion(result: CheckResult, candidate: Candidate,
                     shares: list[tuple[float, Known]]) -> CheckResult:
    """For a file that states no doc_id: point out a family it may belong to. This only
    informs. Joining a family takes a sidecar written by a person (ARCHITECTURE 3.3)."""
    title = normalise_title(candidate.meta.title)
    if candidate.meta.sources.get("doc_id") != "derived" or not title:
        return result
    matches = [(share, k) for share, k in shares
               if share >= limits.SUGGEST_SHARE and normalise_title(k.title) == title]
    if not matches:
        return result
    share, family = max(matches, key=lambda pair: pair[0])
    return replace(result, reason=(
        f"{result.reason}. Suggestion: possible new version of {family.doc_id} (titles match "
        f"and {share:.0%} of windows overlap); to accept it as one, add a sidecar with "
        f"doc_id: {family.doc_id} and a doc_version newer than {family.version}"))


# ---------------------------------------------------------------- the whole batch
def verify_batch(candidates: list[Candidate], lake: list[Known], pinned: set[str],
                 vectors: WindowVectors,
                 retired: Sequence[dict] = ()) -> list[Verdict]:
    """Verify every candidate, in an order that does not depend on file listing: two
    versions in one batch always resolve the same way, the older one first."""
    current = list(lake)             # the lake, plus what this batch has added so far
    pinned = set(pinned)
    passed_before: list[Candidate] = []
    accepted: list[Candidate] = []
    verdicts = []
    for candidate in sorted(candidates, key=_batch_order):
        verdict = _classify(candidate, lake, current, passed_before, accepted, pinned,
                            vectors, retired)
        if verdict.decision != "quarantined" and verdict.action != "ignored":
            passed_before.append(candidate)
        if verdict.decision == "accepted":
            accepted.append(candidate)
            _remember(current, pinned, verdict)
        verdicts.append(verdict)
    return verdicts


def _batch_order(candidate: Candidate) -> tuple:
    meta = candidate.meta
    return (meta.doc_id, version_order(meta.doc_version), candidate.file, candidate.batch)


def _retired_reason(candidate: Candidate, retired: Sequence[dict]) -> str:
    """Why a file from a read-only folder is out of date: the lake once held it, and it
    was then corrected or deleted on purpose. Without this, a folder that still holds
    the old file would undo that on every run."""
    for gone in retired:
        if gone["raw_sha256"] == candidate.raw_sha256 or (
                candidate.content_sha256 == gone["content_sha256"]
                and candidate.meta.doc_id == gone["doc_id"]):
            what = ("replaced by a corrected copy" if gone["why"] == "replaced"
                    else "deleted")
            return (f"{gone['doc_id']} {gone['version']} with this file was {what} in run "
                    f"{gone['run_id']}, and a read-only folder cannot bring it back; to "
                    "apply it on purpose, deliver it through the inbox")
    return ""


def _classify(candidate: Candidate, before: list[Known], current: list[Known],
              earlier: list[Candidate], accepted: list[Candidate], pinned: set[str],
              vectors: WindowVectors, retired: Sequence[dict]) -> Verdict:
    """All four checks are evaluated and recorded, also after one has failed."""
    doc_id = candidate.meta.doc_id
    quality = check_quality(candidate)
    completeness = check_completeness(candidate)

    # The same document with the same text as before this run: nothing to do.
    same = [k for k in before if k.doc_id == doc_id and candidate.content_sha256
            and k.content_sha256 == candidate.content_sha256]
    gone = (_retired_reason(candidate, retired)
            if candidate.read_only and not candidate.file_problem else "")
    if same and not candidate.file_problem:
        action = "unchanged"
        recency = passed(f"identical to version {same[0].version} already in the data lake")
        if candidate.meta.doc_version != same[0].version:
            recency = passed(f"{recency.reason}; the new version label "
                             f"'{candidate.meta.doc_version}' is not applied")
        uniqueness = passed(f"unchanged: same document and same text as version "
                            f"{same[0].version} in the data lake, so not a duplicate")
    elif gone:
        action = "ignored"
        recency = passed(f"ignored: {gone}")
        uniqueness = passed("not compared: an ignored file changes nothing")
    else:
        family = [k for k in current if k.doc_id == doc_id]
        recency, action = check_recency(candidate, family, doc_id in pinned)
        rival = next((c for c in accepted
                      if (c.meta.doc_id, c.meta.doc_version)
                      == (doc_id, candidate.meta.doc_version)
                      and c.content_sha256 != candidate.content_sha256), None)
        if rival:
            recency = failed(f"{rival.file}, an earlier file of this batch, already "
                             f"delivers version {candidate.meta.doc_version} of {doc_id} "
                             "with different text; deliver only the correct one")
        copy_of = find_exact_duplicate(candidate, current, earlier)
        if copy_of:
            uniqueness = failed(copy_of, 1.0)
        elif not candidate.text:
            uniqueness = failed("cannot compare the text: no extracted text (the raw bytes "
                                "match no other document)")
        elif family:
            uniqueness = passed("same document family as a version in the data lake: no "
                                "near-duplicate test, because a new version is expected to "
                                "resemble the old one")
        else:
            others = [k for k in current if k.doc_id != doc_id]
            uniqueness = check_near_duplicate(candidate, others, vectors)

    checks = {"quality": quality, "completeness": completeness,
              "recency": recency, "uniqueness": uniqueness}
    if any(result.status == "fail" for result in checks.values()):
        return Verdict(candidate, checks, "quarantined")
    decision = "unchanged" if action in ("unchanged", "ignored") else "accepted"
    return Verdict(candidate, checks, decision, action)


def _remember(current: list[Known], pinned: set[str], verdict: Verdict) -> None:
    """Let later files of the same batch see this accepted one."""
    c, action = verdict.candidate, verdict.action
    doc_id, version = c.meta.doc_id, c.meta.doc_version
    if action == "newer":
        current[:] = [replace(k, status="superseded") if k.doc_id == doc_id else k
                      for k in current]
        pinned.discard(doc_id)
    current[:] = [k for k in current if (k.doc_id, k.version) != (doc_id, version)]
    current.append(Known(
        doc_id=doc_id, version=version, file=c.file, title=c.meta.title,
        raw_sha256=c.raw_sha256, content_sha256=c.content_sha256, text=c.text,
        status="superseded" if action == "changed_archived" else "active"))
