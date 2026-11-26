"""The verification stage (A1.1 to A1.8): four checks, quarantine, one report per run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import (
    FakeClient,
    FakeEmbedder,
    edit_words,
    make_pdf,
    prose,
    put_markdown,
    services,
    settings,
)

from pipeline import ingest as ingest_module
from pipeline import sync as sync_module
from pipeline.ingest import run_ingest

CHECKS = ("quality", "completeness", "recency", "uniqueness")


def by_file(report: dict) -> dict[str, dict]:
    return {entry["file"]: entry for entry in report["documents"]}


def statuses(entry: dict) -> dict[str, str]:
    return {name: entry["checks"][name]["status"] for name in CHECKS}


def reason(entry: dict, check: str) -> str:
    return entry["checks"][check]["reason"]


def indexed(client: FakeClient, **wanted: object) -> list[dict]:
    """Metadata of the chunks in the live collection whose values match `wanted`."""
    if "rahbar" not in client.collections:
        return []
    return [row["metadata"] for row in client.collections["rahbar"].rows.values()
            if all(row["metadata"].get(k) == v for k, v in wanted.items())]


def lake_manifest(config) -> dict:  # type: ignore[no-untyped-def]
    return json.loads((Path(config.lake_dir) / "state" / "manifest.json").read_text("utf-8"))


# ---------------------------------------------------------------- order of the stages
def test_verification_runs_before_chunking(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "bad.md", prose(2), source=None)
    events: list[tuple[str, str]] = []
    real_verify, real_chunk = ingest_module.verify_batch, sync_module.chunk

    def spy_verify(candidates, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.extend(("verify", c.file) for c in candidates)
        return real_verify(candidates, *args, **kwargs)

    def spy_chunk(doc, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.append(("chunk", doc.source))
        return real_chunk(doc, *args, **kwargs)

    monkeypatch.setattr(ingest_module, "verify_batch", spy_verify)
    monkeypatch.setattr(sync_module, "chunk", spy_chunk)

    run_ingest(config, services())

    assert sorted(events[:2]) == [("verify", "bad.md"), ("verify", "good.md")]
    # Only the document that passed reaches the chunker.
    assert events[2:] == [("chunk", "good.md")]


def test_nothing_is_chunked_when_verification_itself_fails(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    client = FakeClient()

    def broken_verify(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("verification crashed")

    monkeypatch.setattr(ingest_module, "verify_batch", broken_verify)

    with pytest.raises(RuntimeError):
        run_ingest(config, services(client))

    assert client.collections == {}
    assert (Path(config.lake_dir) / "inbox" / "A" / "good.md").exists()   # not consumed


# ---------------------------------------------------------------- every document, 4 verdicts
def test_every_document_gets_four_verdicts_with_reasons(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "empty.md", "")
    (Path(config.lake_dir) / "inbox" / "A" / "notes.docx").write_bytes(b"PK\x03\x04junk")

    report = run_ingest(config, services()).verification

    assert set(by_file(report)) == {"good.md", "empty.md", "notes.docx"}
    for entry in report["documents"]:
        for check in CHECKS:
            assert entry["checks"][check]["status"] in ("pass", "fail")
            assert entry["checks"][check]["reason"].strip()
        failed = "fail" in statuses(entry).values()
        assert (entry["decision"] == "quarantined") == failed
    assert by_file(report)["good.md"]["decision"] == "accepted"


# ---------------------------------------------------------------- quality
def test_quality_fails_empty(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "empty.md", "")
    client = FakeClient()
    put_markdown(config.lake_dir, "good.md", prose(1))

    entry = by_file(run_ingest(config, services(client)).verification)["empty.md"]

    assert entry["checks"]["quality"]["status"] == "fail"
    assert "no text" in reason(entry, "quality")
    # A check that cannot run without text fails and says why.
    assert entry["checks"]["uniqueness"]["status"] == "fail"
    assert "no extracted text" in reason(entry, "uniqueness")
    assert indexed(client, source="empty.md") == []


def test_quality_fails_garbled(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "symbols.md", " ".join(["@#$ %^& ~~| {}= ``"] * 80))
    put_markdown(config.lake_dir, "cid.md", " ".join(["(cid:12)(cid:40)(cid:7)"] * 80))
    put_markdown(config.lake_dir, "replaced.md",
                 " ".join(["pol�cy r�le att�nd"] * 80))
    put_markdown(config.lake_dir, "good.md", prose(1))

    entries = by_file(run_ingest(config, services()).verification)

    assert "word" in reason(entries["symbols.md"], "quality")
    assert "unreadable characters" in reason(entries["cid.md"], "quality")
    assert "unreadable characters" in reason(entries["replaced.md"], "quality")
    for name in ("symbols.md", "cid.md", "replaced.md"):
        assert entries[name]["checks"]["quality"]["status"] == "fail"
    assert entries["good.md"]["checks"]["quality"]["status"] == "pass"


def test_quality_fails_extraction_error(tmp_path: Path) -> None:
    config = settings(tmp_path)
    inbox = Path(config.lake_dir) / "inbox" / "A"
    inbox.mkdir(parents=True)
    (inbox / "broken.pdf").write_bytes(b"%PDF-1.7\nthis is not really a pdf\n%%EOF\n")
    (inbox / "fake.pdf").write_bytes(b"<html>an error page saved as .pdf</html>")
    (inbox / "sheet.xlsx").write_bytes(b"PK\x03\x04junk")

    entries = by_file(run_ingest_allowing_empty_lake(config).verification)

    assert "extraction failed" in reason(entries["broken.pdf"], "quality")
    assert "does not start like a PDF" in reason(entries["fake.pdf"], "quality")
    assert "unsupported file type" in reason(entries["sheet.xlsx"], "quality")
    assert all(e["decision"] == "quarantined" for e in entries.values())


def run_ingest_allowing_empty_lake(config):  # type: ignore[no-untyped-def]
    """For batches in which nothing is expected to pass: the run reports an empty lake,
    and the verification report is still returned."""
    try:
        return run_ingest(config, services())
    except ingest_module.EmptyLake as exc:
        return exc.result


def test_quality_fails_when_most_pages_have_no_text_and_ocr_is_missing(
        tmp_path: Path, monkeypatch) -> None:
    from pipeline import extract as extract_module

    def no_tesseract(path: str, page: int) -> str:
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(extract_module, "_ocr_page", no_tesseract)
    config = settings(tmp_path)
    inbox = Path(config.lake_dir) / "inbox" / "A"
    make_pdf(inbox / "scan.pdf", [[(20, 500, "cover page")], [], [], []])

    entry = by_file(run_ingest_allowing_empty_lake(config).verification)["scan.pdf"]

    assert entry["checks"]["quality"]["status"] == "fail"
    assert "OCR failed" in reason(entry, "quality")
    assert "tesseract is not installed" in reason(entry, "quality")


# ---------------------------------------------------------------- completeness
@pytest.mark.parametrize("field", ["source", "title", "doc_version"])
def test_completeness_fails_each_missing_field(tmp_path: Path, field: str) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "partial.md", prose(2), **{field: None})
    client = FakeClient()

    entry = by_file(run_ingest(config, services(client)).verification)["partial.md"]

    assert entry["checks"]["completeness"]["status"] == "fail"
    assert field in reason(entry, "completeness")
    assert entry["checks"]["quality"]["status"] == "pass"
    assert entry["decision"] == "quarantined"
    assert indexed(client, source="partial.md") == []


def test_completeness_fails_page_count_when_the_file_cannot_be_read(tmp_path: Path) -> None:
    config = settings(tmp_path)
    inbox = Path(config.lake_dir) / "inbox" / "A"
    inbox.mkdir(parents=True)
    (inbox / "broken.pdf").write_bytes(b"%PDF-1.7\nnot a pdf\n%%EOF\n")

    entry = by_file(run_ingest_allowing_empty_lake(config).verification)["broken.pdf"]

    assert "page count" in reason(entry, "completeness")


def pdf_lines(seed: int) -> list[list[tuple[int, int, str]]]:
    words = prose(seed, 120).split()
    lines = [" ".join(words[i:i + 8]) for i in range(0, len(words), 8)]
    return [[(20, 560 - 12 * n, line) for n, line in enumerate(lines)] for _ in range(1)]


def sidecar(path: Path, **fields: object) -> None:
    text = "".join(f'{k}: "{v}"\n' for k, v in fields.items())
    path.with_name(path.name + ".meta.yaml").write_text(text, encoding="utf-8")


FULL = {"title": "A Rulebook", "source": "https://example.test/r.pdf", "doc_version": "2026-01"}


def test_completeness_fails_truncated_file(tmp_path: Path) -> None:
    config = settings(tmp_path)
    inbox = Path(config.lake_dir) / "inbox" / "A"
    whole = make_pdf(inbox / "whole.pdf", pdf_lines(1))
    sidecar(whole, doc_id="whole", **FULL)
    cut = make_pdf(inbox / "cut.pdf", pdf_lines(2))
    cut.write_bytes(cut.read_bytes()[:-60])                 # the end of the file is lost
    sidecar(cut, doc_id="cut", **FULL)
    short = make_pdf(inbox / "short.pdf", pdf_lines(3))
    sidecar(short, doc_id="short", page_count=12, **FULL)   # 11 pages went missing
    page = inbox / "page.html"
    page.write_text(f"<html><title>Fees</title><body><p>{prose(4)}</p>", encoding="utf-8")
    sidecar(page, doc_id="fees", **FULL)
    resized = put_markdown(config.lake_dir, "resized.md", prose(5))
    sidecar(resized, size_bytes=999999)

    entries = by_file(run_ingest(config, services()).verification)

    assert entries["whole.pdf"]["decision"] == "accepted", entries["whole.pdf"]["checks"]
    assert "%%EOF" in reason(entries["cut.pdf"], "completeness")
    assert "declares 12 pages, the file has 1" in reason(entries["short.pdf"], "completeness")
    assert "</html>" in reason(entries["page.html"], "completeness")
    assert "999999 bytes" in reason(entries["resized.md"], "completeness")
    for name in ("cut.pdf", "short.pdf", "page.html", "resized.md"):
        assert entries[name]["checks"]["completeness"]["status"] == "fail", name


# ---------------------------------------------------------------- recency
def test_recency_newer_version_supersedes(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    old_text = prose(1)
    put_markdown(config.lake_dir, "rules-2023.md", old_text, doc_id="ug-rules",
                 doc_version="2023-08")
    run_ingest(config, services(client))

    put_markdown(config.lake_dir, "rules-2026.md", edit_words(old_text, 9), doc_id="ug-rules",
                 doc_version="2026-06")
    entry = by_file(run_ingest(config, services(client)).verification)["rules-2026.md"]

    assert entry["decision"] == "accepted"
    assert "newer than the active version 2023-08" in reason(entry, "recency")
    versions = lake_manifest(config)["documents"]["ug-rules"]["versions"]
    assert versions["2023-08"]["status"] == "superseded"
    assert versions["2026-06"]["status"] == "active"
    assert {m["superseded"] for m in indexed(client, doc_version="2023-08")} == {True}
    assert {m["superseded"] for m in indexed(client, doc_version="2026-06")} == {False}
    lake = Path(config.lake_dir)
    assert (lake / "archive" / "ug-rules" / "2023-08" / "meta.json").is_file()
    assert (lake / "lake" / "ug-rules" / "2026-06" / "meta.json").is_file()
    assert not (lake / "lake" / "ug-rules" / "2023-08").exists()


def test_two_versions_in_one_batch_resolve_by_version_not_by_file_name(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "a-new.md", prose(1), doc_id="rules", doc_version="2026-06")
    put_markdown(config.lake_dir, "z-old.md", prose(2), doc_id="rules", doc_version="2023-08")

    entries = by_file(run_ingest(config, services()).verification)

    assert {e["decision"] for e in entries.values()} == {"accepted"}
    versions = lake_manifest(config)["documents"]["rules"]["versions"]
    assert [versions[v]["status"] for v in ("2023-08", "2026-06")] == ["superseded", "active"]


def test_recency_stale_version_fails(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "rules-2026.md", prose(1), doc_id="rules",
                 doc_version="2026-06")
    run_ingest(config, services(client))

    put_markdown(config.lake_dir, "rules-2023.md", prose(2), doc_id="rules",
                 doc_version="2023-08")
    entry = by_file(run_ingest(config, services(client)).verification)["rules-2023.md"]

    assert entry["checks"]["recency"]["status"] == "fail"
    assert "older than the active version 2026-06" in reason(entry, "recency")
    assert entry["decision"] == "quarantined"
    assert indexed(client, doc_version="2023-08") == []
    assert list(lake_manifest(config)["documents"]["rules"]["versions"]) == ["2026-06"]


def test_recency_fails_without_a_usable_version(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "odd.md", prose(2), doc_version="Fall edition")

    entry = by_file(run_ingest(config, services()).verification)["odd.md"]

    assert entry["checks"]["recency"]["status"] == "fail"
    assert "Fall edition" in reason(entry, "completeness")


def test_recency_does_not_merge_annual_reports(tmp_path: Path) -> None:
    """Same publisher, nearly the same title, a later year: still two documents."""
    config = settings(tmp_path)
    client = FakeClient()
    shared = prose(1, 120)                          # campus descriptions repeat every year
    put_markdown(config.lake_dir, "annual-report-2019-20.md", shared + "\n\n" + prose(2),
                 doc_id="annual-report-2019-20", doc_version="2019-20", title="Annual Report")
    run_ingest(config, services(client))
    put_markdown(config.lake_dir, "annual-report-2023-24.md", shared + "\n\n" + prose(3),
                 doc_id="annual-report-2023-24", doc_version="2023-24", title="Annual Report")

    entry = by_file(run_ingest(config, services(client)).verification)[
        "annual-report-2023-24.md"]

    assert entry["decision"] == "accepted", entry["checks"]
    documents = lake_manifest(config)["documents"]
    assert documents["annual-report-2019-20"]["versions"]["2019-20"]["status"] == "active"
    assert documents["annual-report-2023-24"]["versions"]["2023-24"]["status"] == "active"
    assert {m["superseded"] for m in indexed(client)} == {False}


def test_annual_reports_without_sidecars_are_not_merged_either(tmp_path: Path) -> None:
    config = settings(tmp_path)
    for year, seed in (("2019-20", 2), ("2023-24", 3)):
        put_markdown(config.lake_dir, f"annual-report-{year}.md", prose(seed),
                     doc_id=None, doc_version=year, title="Annual Report")

    entries = by_file(run_ingest(config, services()).verification)

    ids = {e["doc_id"] for e in entries.values()}
    assert len(ids) == 2
    assert {e["decision"] for e in entries.values()} == {"accepted"}
    assert {e["metadata_source"]["doc_id"] for e in entries.values()} == {"derived"}


# ---------------------------------------------------------------- uniqueness
def test_uniqueness_exact_duplicate_by_hash(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    original = put_markdown(config.lake_dir, "original.md", prose(1))
    raw = original.read_bytes()
    run_ingest(config, services(client))

    inbox = Path(config.lake_dir) / "inbox" / "B"
    inbox.mkdir(parents=True)
    # 1: the same bytes under a new name and a new identity.
    (inbox / "copy.md").write_bytes(raw)
    sidecar(inbox / "copy.md", doc_id="something-else")
    # 2: the same text in a different container, so the bytes differ.
    container = inbox / "same-text.html"
    paragraphs = "".join(f"<p>{p}</p>" for p in prose(1).split("\n\n"))
    container.write_text(f"<html><body>{paragraphs}</body></html>", encoding="utf-8")
    sidecar(container, doc_id="as-web-page", **FULL)

    entries = by_file(run_ingest(config, services(client)).verification)

    assert "raw bytes" in reason(entries["copy.md"], "uniqueness")
    assert "same text" in reason(entries["same-text.html"], "uniqueness")
    for entry in entries.values():
        assert entry["checks"]["uniqueness"]["status"] == "fail"
        assert entry["checks"]["uniqueness"]["score"] == 1.0
        assert "original" in reason(entry, "uniqueness")
    assert indexed(client, doc_id="something-else") == []
    assert indexed(client, doc_id="as-web-page") == []


def test_exact_duplicate_inside_one_batch_keeps_one_copy(tmp_path: Path) -> None:
    config = settings(tmp_path)
    first = put_markdown(config.lake_dir, "first.md", prose(1), doc_id=None)
    first.with_name("second.md").write_bytes(first.read_bytes())

    entries = by_file(run_ingest(config, services()).verification)

    assert sorted(e["decision"] for e in entries.values()) == ["accepted", "quarantined"]
    assert "earlier file in this batch" in reason(entries["second.md"], "uniqueness")


def test_a_rejected_copy_does_not_get_the_good_original_rejected(tmp_path: Path) -> None:
    """A copy with no metadata sorts first and fails completeness. The complete original
    must still be accepted."""
    config = settings(tmp_path)
    body = prose(1)
    put_markdown(config.lake_dir, "aaa-copy.md", body, doc_id="aaa", source=None)
    put_markdown(config.lake_dir, "original.md", body, doc_id="zzz")

    entries = by_file(run_ingest(config, services()).verification)

    assert entries["aaa-copy.md"]["decision"] == "quarantined"
    assert entries["original.md"]["decision"] == "accepted"


def test_uniqueness_near_duplicate_by_embedding(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    embedder = FakeEmbedder()
    original = prose(1, 600)
    put_markdown(config.lake_dir, "rules.md", original)
    put_markdown(config.lake_dir, "other.md", prose(7, 600))
    run_ingest(config, services(client, embedder))
    embedder.reset()

    put_markdown(config.lake_dir, "rewrite.md", edit_words(original, 40), batch="B",
                 doc_id="a-different-identity")
    put_markdown(config.lake_dir, "fresh.md", prose(11, 600), batch="B")
    entries = by_file(run_ingest(config, services(client, embedder)).verification)

    duplicate = entries["rewrite.md"]["checks"]["uniqueness"]
    assert duplicate["status"] == "fail"
    assert duplicate["score"] >= 0.8
    assert "near-duplicate of rules" in duplicate["reason"]
    assert indexed(client, doc_id="a-different-identity") == []
    fresh = entries["fresh.md"]["checks"]["uniqueness"]
    assert fresh["status"] == "pass"
    assert fresh["score"] < 0.2
    # Verification embeds with its own fixed model, whatever the release uses.
    assert {model for model, _ in embedder.calls} == {config.verify_embed_model,
                                                      config.embed_model}


def test_verification_uses_its_own_model_not_the_release_model(tmp_path: Path) -> None:
    config = settings(tmp_path, embed_model="mxbai-embed-large",
                      verify_embed_model="nomic-embed-text")
    embedder = FakeEmbedder()
    put_markdown(config.lake_dir, "one.md", prose(1))
    put_markdown(config.lake_dir, "two.md", prose(2))

    result = run_ingest(config, services(embedder=embedder))

    by_model: dict[str, int] = {}
    for model, _ in embedder.calls:
        by_model[model] = by_model.get(model, 0) + 1
    assert by_model["nomic-embed-text"] == result.ingest[0]["counts"][
        "verification_embedding_calls"] > 0
    assert by_model["mxbai-embed-large"] > 0


def test_resubmitted_unchanged_doc_is_not_a_duplicate(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    embedder = FakeEmbedder()
    for seed, name in ((1, "one.md"), (2, "two.md"), (3, "three.md")):
        put_markdown(config.lake_dir, name, prose(seed))
    run_ingest(config, services(client, embedder))
    chunks_before = sorted(client.collections["rahbar"].rows)
    embedder.reset()

    # The whole corpus is delivered again.
    for seed, name in ((1, "one.md"), (2, "two.md"), (3, "three.md")):
        put_markdown(config.lake_dir, name, prose(seed))
    result = run_ingest(config, services(client, embedder))

    entries = by_file(result.verification)
    assert {e["decision"] for e in entries.values()} == {"unchanged"}
    for entry in entries.values():
        assert set(statuses(entry).values()) == {"pass"}
        assert "unchanged" in reason(entry, "uniqueness")
    assert embedder.calls == []
    assert result.ingest[0]["counts"]["verification_embedding_calls"] == 0
    assert sorted(client.collections["rahbar"].rows) == chunks_before
    assert not list((Path(config.lake_dir) / "quarantine").rglob("*.md"))


def test_new_version_is_not_a_near_duplicate(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    text = prose(1, 600)
    put_markdown(config.lake_dir, "v1.md", text, doc_id="policy", doc_version="2025")
    run_ingest(config, services(client))

    put_markdown(config.lake_dir, "v2.md", edit_words(text, 200), doc_id="policy",
                 doc_version="2026")
    entry = by_file(run_ingest(config, services(client)).verification)["v2.md"]

    assert entry["decision"] == "accepted", entry["checks"]
    assert "same document family" in reason(entry, "uniqueness")


def test_update_without_sidecar_is_only_suggested_as_a_new_version(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    text = prose(1, 600)
    put_markdown(config.lake_dir, "policy.md", text, doc_id="policy", title="Leave Policy",
                 doc_version="2025")
    run_ingest(config, services(client))

    put_markdown(config.lake_dir, "policy-new.md", edit_words(text, 40), doc_id=None,
                 title="Leave  policy 2026", doc_version="2026")
    entry = by_file(run_ingest(config, services(client)).verification)["policy-new.md"]

    assert entry["decision"] == "quarantined"
    text_of_reason = reason(entry, "uniqueness")
    assert "possible new version of policy" in text_of_reason
    assert "doc_id: policy" in text_of_reason
    assert "titles match" in text_of_reason
    versions = lake_manifest(config)["documents"]["policy"]["versions"]
    assert list(versions) == ["2025"] and versions["2025"]["status"] == "active"


# ---------------------------------------------------------------- quarantine
def test_failed_update_keeps_previous_version_active(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "policy.md", prose(1), doc_id="policy", doc_version="2025")
    run_ingest(config, services(client))
    good_chunks = sorted(client.collections["rahbar"].rows)

    # A newer version arrives broken: no text at all. Same file name as the good one.
    put_markdown(config.lake_dir, "policy.md", "", doc_id="policy", doc_version="2026")
    result = run_ingest(config, services(client))

    [entry] = result.verification["documents"]
    assert entry["decision"] == "quarantined"
    assert sorted(client.collections["rahbar"].rows) == good_chunks
    assert {m["superseded"] for m in indexed(client, doc_id="policy")} == {False}
    versions = lake_manifest(config)["documents"]["policy"]["versions"]
    assert list(versions) == ["2025"] and versions["2025"]["status"] == "active"
    quarantined = Path(config.lake_dir) / "quarantine" / result.run_id / "policy.md"
    assert (quarantined / "policy.md").is_file()
    why = json.loads((quarantined / "reason.json").read_text("utf-8"))
    assert "quality" in why["failed_checks"]
    assert not (Path(config.lake_dir) / "inbox" / "A" / "policy.md").exists()


def test_quarantine_proof_counts_chunks_in_the_vector_store(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "nosource.md", prose(2), source=None)
    run_ingest(config, services(client))
    proof_path = Path(config.report_dir) / "proofs" / "quarantine.json"

    proof = json.loads(proof_path.read_text("utf-8"))

    [item] = proof["documents"]
    assert (item["file"], item["in_quarantine"], item["indexed_chunks"]) == (
        "nosource.md", True, 0)
    assert "source" in item["reason"]

    # The count is read from the store: plant a chunk of the rejected document and the
    # proof must report it.
    from pipeline.admin import quarantine_proof

    collection = client.collections["rahbar"]
    collection.upsert(ids=["planted"], embeddings=[[0.0]], documents=["x"],
                      metadatas=[{"doc_id": item["doc_id"],
                                  "content_sha256": item["content_sha256"]}])
    assert quarantine_proof(config, services(client))["documents"][0]["indexed_chunks"] == 1


# ---------------------------------------------------------------- reports
def test_each_run_writes_its_own_report(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "one.md", prose(1))
    client = FakeClient()
    first = run_ingest(config, services(client))
    second = run_ingest(config, services(client))                # nothing new in the inbox
    put_markdown(config.lake_dir, "bad.md", prose(2), batch="B", title=None)
    third = run_ingest(config, services(client))

    folder = Path(config.report_dir) / "verification"
    files = sorted(folder.glob("*.json"))
    assert len(files) == 3
    assert len({first.run_id, second.run_id, third.run_id}) == 3
    reports = [json.loads(f.read_text("utf-8")) for f in files]
    assert [r["run_id"] for r in reports] == [first.run_id, second.run_id, third.run_id]
    assert [r["batch"] for r in reports] == ["A", "", "B"]
    assert [len(r["documents"]) for r in reports] == [1, 0, 1]
    assert all(r["started_at"] for r in reports)
    for path in files:
        table = path.with_suffix(".md").read_text("utf-8")
        assert "| File |" in table and "Uniqueness" in table
    assert "bad.md" in files[2].with_suffix(".md").read_text("utf-8")
    assert "title" in files[2].with_suffix(".md").read_text("utf-8")


def test_window_vectors_are_cached_so_a_repeat_costs_no_embedding_call(tmp_path: Path) -> None:
    """A rejected near-duplicate delivered again (as a read-only folder does on every
    run) is judged from cached window vectors."""
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    original = prose(1, 600)
    put_markdown(config.lake_dir, "rules.md", original)
    run_ingest(config, services(client, embedder))

    reasons = []
    for _ in range(2):
        embedder.reset()
        put_markdown(config.lake_dir, "rewrite.md", edit_words(original, 40), batch="B",
                     doc_id="another")
        result = run_ingest(config, services(client, embedder))
        [entry] = result.verification["documents"]
        reasons.append(entry["checks"]["uniqueness"]["reason"])

    assert embedder.calls == []
    assert result.verification["verification_embedding_calls"] == 0
    assert reasons[0] == reasons[1] and "near-duplicate of rules" in reasons[1]


def test_windows_cover_every_word_without_a_tiny_tail() -> None:
    from pipeline.neardup import split_windows

    words = [f"w{n}" for n in range(101)]

    windows = split_windows(" ".join(words), size=100, stride=50)

    assert [w.split()[0] for w in windows] == ["w0", "w50"]
    assert windows[-1].split()[-1] == "w100"
    assert split_windows("one two", size=100, stride=50) == ["one two"]
    assert split_windows("", size=100, stride=50) == []
