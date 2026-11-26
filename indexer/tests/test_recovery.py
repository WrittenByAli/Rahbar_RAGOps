"""Things that go wrong: crashes between two writes, broken input files, stale read-only
sources, a stolen lock and a store that does not do what it is asked."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fakes import FakeClient, FakeEmbedder, make_pdf, prose, put_markdown, services, settings

from pipeline import ingest as ingest_module
from pipeline import lake as lake_module
from pipeline import run as cli
from pipeline.admin import delete_document, restore_version
from pipeline.ingest import run_ingest
from pipeline.lake import Lake
from pipeline.lock import LockLost
from pipeline.report import Reports
from pipeline.sync import ForeignCollection


def rows(client: FakeClient, **wanted: object) -> list[dict]:
    if "rahbar" not in client.collections:
        return []
    return [r for r in client.collections["rahbar"].rows.values()
            if all(r["metadata"].get(k) == v for k, v in wanted.items())]


def texts(client: FakeClient, **wanted: object) -> str:
    return " ".join(r["document"] for r in rows(client, **wanted))


def retrievable(client: FakeClient, doc_id: str) -> list[str]:
    return sorted({r["metadata"]["doc_version"]
                   for r in rows(client, doc_id=doc_id, superseded=False)})


def manifest(config) -> dict:  # type: ignore[no-untyped-def]
    return json.loads((Path(config.lake_dir) / "state" / "manifest.json").read_text("utf-8"))


def lake_copy_matches_manifest(config, doc_id: str, version: str) -> bool:  # type: ignore[no-untyped-def]
    entry = manifest(config)["documents"][doc_id]["versions"][version]
    root = "lake" if entry["status"] == "active" else "archive"
    folder = Path(config.lake_dir) / root / doc_id / version
    meta = json.loads((folder / "meta.json").read_text("utf-8"))
    return meta["raw_sha256"] == entry["raw_sha256"] and (folder / entry["file"]).is_file()


def policy(config, text: str, version: str = "2025", name: str = "policy.md",  # type: ignore[no-untyped-def]
           batch: str = "A") -> Path:
    return put_markdown(config.lake_dir, name, text, batch=batch, doc_id="policy",
                        doc_version=version)


def fail_once(monkeypatch, owner: object, name: str, when=lambda *a: True) -> None:  # type: ignore[no-untyped-def]
    real = getattr(owner, name)
    done: list[bool] = []

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        if not done and when(*args):
            done.append(True)
            raise OSError("pod killed here")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)


# ---------------------------------------------------------------- 1: crashes in the lake
@pytest.mark.parametrize("kind", ["new", "newer", "corrected"])
def test_a_crash_before_the_manifest_save_is_repaired_by_the_next_run(
        tmp_path: Path, monkeypatch, kind: str) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "other.md", prose(9))
    if kind != "new":
        policy(config, prose(1))
    run_ingest(config, services(client))
    version = "2026" if kind == "newer" else "2025"
    policy(config, prose(2), version=version)
    fail_once(monkeypatch, Lake, "save")
    with pytest.raises(OSError, match="killed"):
        run_ingest(config, services(client))

    result = run_ingest(config, services(client))

    assert [e["decision"] for e in result.verification["documents"]] == ["accepted"]
    assert retrievable(client, "policy") == [version]
    assert prose(2).split()[0] in texts(client, doc_id="policy", superseded=False)
    for stored in manifest(config)["documents"]["policy"]["versions"]:
        assert lake_copy_matches_manifest(config, "policy", stored), stored
    assert run_ingest(config, services(client)).ingest[0]["counts"]["chunks_embedded"] == 0


@pytest.mark.parametrize("delivered_again", [True, False])
def test_a_crash_while_a_corrected_copy_replaces_the_old_one_is_recoverable(
        tmp_path: Path, monkeypatch, delivered_again: bool) -> None:
    """The old folder is set aside, then the new one is moved in. A kill between the two
    leaves the manifest pointing at an empty place."""
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(1))
    run_ingest(config, services(client))
    correction = policy(config, prose(2))
    fail_once(monkeypatch, lake_module, "_move",
              when=lambda source, target: "staging" in Path(source).parts)
    with pytest.raises(OSError, match="killed"):
        run_ingest(config, services(client))
    if not delivered_again:
        correction.unlink()

    run_ingest(config, services(client))

    expected = prose(2) if delivered_again else prose(1)
    assert expected.split()[0] in texts(client, doc_id="policy", superseded=False)
    assert lake_copy_matches_manifest(config, "policy", "2025")


# ---------------------------------------------------------------- 2: broken input files
def bad_sidecar(path: Path, text: str) -> None:
    path.with_name(path.name + ".meta.yaml").write_text(text, encoding="utf-8")


BASE = "doc_id: broken\ntitle: T\nsource: s\n"
BROKEN_SIDECARS = {
    "impossible-date": BASE + "doc_version: 2026-13-45\n",
    "infinite-pages": BASE + "doc_version: '2026'\npage_count: .inf\n",
    "deeply-nested": "title: " + "[" * 5000 + "]" * 5000 + "\n",
    "surrogate-title": 'doc_id: broken\ntitle: "bad \\ud800 title"\nsource: s\n'
                       "doc_version: '2026'\n",
    "surrogate-source": 'doc_id: broken\ntitle: T\nsource: "x\\udfff"\n'
                        "doc_version: '2026'\n",
}


@pytest.mark.parametrize("case", sorted(BROKEN_SIDECARS))
def test_a_broken_sidecar_quarantines_that_file_only(tmp_path: Path, case: str) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "good.md", prose(1))
    broken = put_markdown(config.lake_dir, "broken.md", prose(2), doc_id=None, title=None,
                          source=None, doc_version=None)
    broken.write_text(prose(2), encoding="utf-8")
    bad_sidecar(broken, BROKEN_SIDECARS[case])

    result = run_ingest(config, services(client))

    entries = {e["file"]: e for e in result.verification["documents"]}
    assert entries["good.md"]["decision"] == "accepted"
    assert entries["broken.md"]["decision"] == "quarantined"
    assert any(c["status"] == "fail" and c["reason"]
               for c in entries["broken.md"]["checks"].values())
    assert rows(client, source="good.md") and not rows(client, source="broken.md")
    assert not broken.exists()                               # it left the inbox
    assert list((Path(config.lake_dir) / "quarantine").glob("*/broken.md*/reason.json"))


def test_front_matter_with_an_impossible_date_quarantines_that_file_only(
        tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    broken = Path(config.lake_dir) / "inbox" / "A" / "broken.md"
    broken.write_text("---\ntitle: T\nsource: s\ndoc_version: 2026-02-30\n---\n" + prose(2),
                      encoding="utf-8")

    entries = {e["file"]: e
               for e in run_ingest(config, services()).verification["documents"]}

    assert entries["good.md"]["decision"] == "accepted"
    assert entries["broken.md"]["decision"] == "quarantined"
    assert "front matter" in entries["broken.md"]["checks"]["completeness"]["reason"]


def test_a_surrogate_in_the_pdf_title_does_not_abort_the_run(tmp_path: Path,
                                                             monkeypatch) -> None:
    from pipeline import metadata as metadata_module

    monkeypatch.setattr(metadata_module, "_pdf_info",
                        lambda path: {"title": "bad \ud800 title"})
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    pdf = make_pdf(Path(config.lake_dir) / "inbox" / "A" / "odd.pdf",
                   [[(20, 500 - 12 * n, prose(3 + n, 8).replace("\n", " "))
                     for n in range(12)]])
    bad_sidecar(pdf, "doc_id: odd\nsource: s\ndoc_version: '2026'\n")

    entries = {e["file"]: e
               for e in run_ingest(config, services()).verification["documents"]}

    assert entries["good.md"]["decision"] == "accepted"
    assert entries["odd.pdf"]["decision"] == "quarantined"
    assert "title" in entries["odd.pdf"]["checks"]["completeness"]["reason"]


@pytest.mark.parametrize("how", ["inbox", "source", "corpus_dir"])
def test_a_text_file_that_is_not_utf8_is_quarantined(tmp_path: Path, how: str) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    config = settings(tmp_path, corpus_dir=str(folder) if how == "corpus_dir" else "")
    target = Path(config.lake_dir) / "inbox" / "A" if how == "inbox" else folder
    good = put_markdown(tmp_path / "scratch", "good.md", prose(1))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(good, target / "good.md")
    (target / "latin.md").write_bytes("---\ntitle: café\n---\nrèglement".encode("latin-1"))

    result = run_ingest(config, services(), sources=[folder] if how == "source" else None)

    entries = {e["file"]: e for e in result.verification["documents"]}
    assert entries["good.md"]["decision"] == "accepted"
    assert "not valid UTF-8" in entries["latin.md"]["checks"]["quality"]["reason"]


def test_any_unexpected_error_on_one_file_becomes_a_quarantine_reason(tmp_path: Path,
                                                                      monkeypatch) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "cursed.md", prose(2))
    real = ingest_module.resolve

    def explode(path: Path, raw_sha256: str):  # type: ignore[no-untyped-def]
        if path.name == "cursed.md":
            raise ZeroDivisionError("something nobody thought of")
        return real(path, raw_sha256)

    monkeypatch.setattr(ingest_module, "resolve", explode)

    entries = {e["file"]: e
               for e in run_ingest(config, services()).verification["documents"]}

    assert entries["good.md"]["decision"] == "accepted"
    assert entries["cursed.md"]["decision"] == "quarantined"
    assert "ZeroDivisionError" in entries["cursed.md"]["checks"]["quality"]["reason"]


# ---------------------------------------------------------------- 3: order of the writes
def test_a_failing_upsert_leaves_the_old_version_retrievable(tmp_path: Path,
                                                             monkeypatch) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(1), version="2025")
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    policy(config, prose(2), version="2026")

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConnectionError("upsert failed")

    monkeypatch.setattr(collection, "upsert", refuse)
    with pytest.raises(ConnectionError):
        run_ingest(config, services(client))

    assert retrievable(client, "policy") == ["2025"]


def test_all_upserts_come_before_any_update_and_any_delete(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    for name, seed in (("aaa.md", 1), ("zzz.md", 2)):
        put_markdown(config.lake_dir, name, prose(seed))
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.writes.clear()
    for name, seed in (("aaa.md", 3), ("zzz.md", 4)):
        put_markdown(config.lake_dir, name, prose(seed))
    collection.rows[next(iter(collection.rows))]["metadata"]["page_end"] = 7

    run_ingest(config, services(client))

    kinds = [w.split()[0] for w in collection.writes if w != "modify"]
    assert kinds == sorted(kinds, key=["upsert", "update", "delete"].index), kinds


# ---------------------------------------------------------------- 4: stale read-only files
def source_with(tmp_path: Path, text: str) -> Path:
    folder = tmp_path / "corpus"
    folder.mkdir(exist_ok=True)
    made = put_markdown(tmp_path / "scratch", "policy.md", text, doc_id="policy",
                        doc_version="2025")
    shutil.copyfile(made, folder / "policy.md")
    return folder


def test_a_read_only_file_cannot_revert_a_corrected_copy(tmp_path: Path) -> None:
    folder = source_with(tmp_path, prose(1))
    config = settings(tmp_path, corpus_dir=str(folder))
    client, embedder = FakeClient(), FakeEmbedder()
    run_ingest(config, services(client, embedder))
    policy(config, prose(2), name="policy-fixed.md")
    run_ingest(config, services(client, embedder))
    corrected = texts(client, doc_id="policy")
    embedder.reset()

    for _ in range(2):
        result = run_ingest(config, services(client, embedder))

    assert texts(client, doc_id="policy") == corrected
    [entry] = result.verification["documents"]
    assert (entry["decision"], entry["action"]) == ("unchanged", "ignored")
    assert "replaced" in entry["checks"]["recency"]["reason"]
    assert result.ingest[0]["counts"]["chunks_embedded"] == 0
    assert embedder.calls == []


def test_a_read_only_file_cannot_bring_a_deleted_document_back(tmp_path: Path) -> None:
    folder = source_with(tmp_path, prose(1))
    config = settings(tmp_path, corpus_dir=str(folder))
    client = FakeClient()
    put_markdown(config.lake_dir, "other.md", prose(9))
    run_ingest(config, services(client))
    delete_document(config, services(client), "policy")

    result = run_ingest(config, services(client))

    assert not rows(client, doc_id="policy")
    assert "policy" not in manifest(config)["documents"]
    [entry] = result.verification["documents"]
    assert entry["action"] == "ignored"
    assert "deleted" in entry["checks"]["recency"]["reason"]


def test_the_inbox_may_deliver_a_deleted_document_again(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "other.md", prose(9))
    policy(config, prose(1))
    run_ingest(config, services(client))
    delete_document(config, services(client), "policy")

    policy(config, prose(1))
    run_ingest(config, services(client))

    assert retrievable(client, "policy") == ["2025"]


# ---------------------------------------------------------------- 5: the report
def test_a_very_long_batch_label_still_gives_a_report(tmp_path: Path) -> None:
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "one.md", prose(1))
    label = "batch-" + "x" * 400

    result = run_ingest(config, services(), batch=label)

    [path] = (Path(config.report_dir) / "verification").glob("*.json")
    assert len(path.name) < 100
    assert result.verification["batch"] == label
    assert json.loads(path.read_text("utf-8"))["batch"] == label


def test_the_report_is_written_before_inbox_files_are_removed(tmp_path: Path,
                                                              monkeypatch) -> None:
    config = settings(tmp_path)
    delivered = put_markdown(config.lake_dir, "one.md", prose(1))
    fail_once(monkeypatch, Reports, "verification")

    with pytest.raises(OSError):
        run_ingest(config, services())

    assert delivered.exists()
    run_ingest(config, services())
    assert not delivered.exists()


# ---------------------------------------------------------------- 6: the quarantine proof
def test_redelivery_of_indexed_text_is_reported_apart_with_its_real_count(
        tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(1))
    first = run_ingest(config, services(client))
    stored = len(rows(client, doc_id="policy"))
    # The same text again, now without its source: quarantined, yet already indexed.
    put_markdown(config.lake_dir, "policy.md", prose(1), doc_id="policy",
                 doc_version="2025", source=None)
    put_markdown(config.lake_dir, "other.md", prose(2), source=None)

    second = run_ingest(config, services(client))

    proof = json.loads(
        (Path(config.report_dir) / "proofs" / "quarantine.json").read_text("utf-8"))
    assert [d["file"] for d in proof["documents"]] == ["other.md"]
    assert proof["documents"][0]["indexed_chunks"] == 0
    [apart] = proof["already_indexed"]
    assert apart["file"] == "policy.md" and apart["in_quarantine"] is True
    assert apart["indexed_chunks"] == stored > 0
    assert apart["chunks_in_store_before_this_delivery"] == stored
    assert apart["chunks_added_since"] == 0
    assert apart["indexed_by_run"] == first.run_id < second.run_id == apart["run_id"]


def test_chunks_that_were_not_there_before_still_count_against_the_proof(
        tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "good.md", prose(1))
    put_markdown(config.lake_dir, "bad.md", prose(2), source=None)
    run_ingest(config, services(client))
    proof_path = Path(config.report_dir) / "proofs" / "quarantine.json"
    [item] = json.loads(proof_path.read_text("utf-8"))["documents"]
    client.collections["rahbar"].upsert(
        ids=["leak"], embeddings=[[0.0]], documents=["x"],
        metadatas=[{"doc_id": item["doc_id"], "content_sha256": item["content_sha256"]}])
    from pipeline.admin import quarantine_proof

    proof = quarantine_proof(config, services(client))

    assert proof["documents"][0]["indexed_chunks"] == 1
    assert proof["already_indexed"] == []


# ---------------------------------------------------------------- 7: collections without owner
def test_a_collection_without_an_owner_is_not_swept_without_adoption(tmp_path: Path) -> None:
    """For example the Assignment 02 index: real chunks, no lake_id."""
    client = FakeClient()
    legacy = client.create_collection("rahbar", {"embed_model": "nomic-embed-text"})
    legacy.add(ids=["a2-1", "a2-2"], embeddings=[[1.0], [1.0]], documents=["x", "y"],
               metadatas=[{"source": "old.pdf"}, {"source": "old.pdf"}])
    config = settings(tmp_path)
    put_markdown(config.lake_dir, "one.md", prose(1))

    with pytest.raises(ForeignCollection):
        run_ingest(config, services(client))

    assert sorted(legacy.rows) == ["a2-1", "a2-2"]
    run_ingest(config, services(client), adopt=True)
    assert "a2-1" not in client.collections["rahbar"].rows


# ---------------------------------------------------------------- 8: one version, two texts
def test_two_different_files_for_one_version_in_a_batch_fail_the_second(
        tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(2), name="b-second.md")
    policy(config, prose(1), name="a-first.md")

    entries = {e["file"]: e
               for e in run_ingest(config, services(client)).verification["documents"]}

    assert entries["a-first.md"]["decision"] == "accepted"
    assert entries["b-second.md"]["decision"] == "quarantined"
    assert "a-first.md" in entries["b-second.md"]["checks"]["recency"]["reason"]
    assert prose(1).split()[0] in texts(client, doc_id="policy")
    assert prose(2).split()[0] not in texts(client, doc_id="policy")


# ---------------------------------------------------------------- 9: lock taken mid-run
def steal(config) -> None:  # type: ignore[no-untyped-def]
    (Path(config.lake_dir) / "state" / "ingest.lock").write_text(
        json.dumps({"token": "another-run"}), encoding="utf-8")


def test_lock_stolen_during_verification_stops_before_the_lake_changes(
        tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    delivered = put_markdown(config.lake_dir, "one.md", prose(1))
    real = ingest_module.verify_batch

    def verify_then_lose_the_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
        verdicts = real(*args, **kwargs)
        steal(config)
        return verdicts

    monkeypatch.setattr(ingest_module, "verify_batch", verify_then_lose_the_lock)

    with pytest.raises(LockLost):
        run_ingest(config, services(client))

    assert delivered.exists() and client.collections == {}
    assert not (Path(config.lake_dir) / "state" / "manifest.json").exists()
    assert not (Path(config.lake_dir) / "lake").exists()


def test_lock_stolen_during_embedding_stops_before_the_first_write(tmp_path: Path) -> None:
    # Verification gets its own model name, so the theft happens while chunks are being
    # embedded: after the lake was updated and before the first write to the collection.
    config = settings(tmp_path, verify_embed_model="verification-only")
    client = FakeClient()
    put_markdown(config.lake_dir, "one.md", prose(1))
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.writes.clear()
    put_markdown(config.lake_dir, "two.md", prose(2))
    stolen: list[str] = []

    class ThiefEmbedder(FakeEmbedder):
        def __call__(self, batch: list[str], model: str) -> list[list[float]]:
            if model == config.embed_model:
                steal(config)
                stolen.append(model)
            return super().__call__(batch, model)

    with pytest.raises(LockLost):
        run_ingest(config, services(client, ThiefEmbedder()))

    assert stolen and collection.writes == []


def test_lock_stolen_between_writes_stops_the_remaining_ones(tmp_path: Path,
                                                             monkeypatch) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(1), version="2025")
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    real_upsert = collection.upsert

    def upsert_then_lose_the_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
        real_upsert(*args, **kwargs)
        steal(config)

    monkeypatch.setattr(collection, "upsert", upsert_then_lose_the_lock)
    collection.writes.clear()
    policy(config, prose(2), version="2026")

    with pytest.raises(LockLost):
        run_ingest(config, services(client))

    assert [w for w in collection.writes if w.startswith(("update", "delete"))] == []
    assert collection.metadata["write_in_progress"] is True


@pytest.mark.parametrize("command", ["delete", "restore"])
def test_lock_stolen_during_delete_or_restore_stops_the_store_writes(
        tmp_path: Path, monkeypatch, command: str) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    policy(config, prose(1), version="2025")
    run_ingest(config, services(client))
    policy(config, prose(2), version="2026")
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.writes.clear()
    real_save = Lake.save

    def save_then_lose_the_lock(self: Lake) -> None:
        real_save(self)
        steal(config)

    monkeypatch.setattr(Lake, "save", save_then_lose_the_lock)

    with pytest.raises(LockLost):
        if command == "delete":
            delete_document(config, services(client), "policy")
        else:
            restore_version(config, services(client), "policy", "2025")

    assert collection.writes == []


# ---------------------------------------------------------------- 9: a delete that does nothing
def test_delete_reports_what_the_store_really_holds(tmp_path: Path, monkeypatch,
                                                    capsys) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "other.md", prose(9))
    policy(config, prose(1))
    run_ingest(config, services(client))
    stored = len(rows(client, doc_id="policy"))
    monkeypatch.setattr(client.collections["rahbar"], "delete", lambda **kwargs: None)
    monkeypatch.setattr(cli, "get_settings", lambda: config)
    monkeypatch.setattr(cli, "real_services", lambda _s: services(client))

    code = cli.main(["delete", "--doc-id", "policy"])

    proof = json.loads(
        (Path(config.report_dir) / "proofs" / "deleted.json").read_text("utf-8"))
    assert code != 0
    assert (proof["count_before"], proof["count_after"]) == (stored, stored)
    assert proof["orphan_chunks_after"] == stored


# ---------------------------------------------------------------- 0: the stamp
def test_delete_restamps_a_target_that_holds_no_chunk_of_the_document(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "other.md", prose(9))
    policy(config, prose(1))
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.delete(where={"doc_id": "policy"})
    before = collection.metadata["lake_state_id"]

    delete_document(config, services(client), "policy")

    lake = Lake(Path(config.lake_dir))
    lake.load()
    assert collection.metadata["lake_state_id"] == lake.state_id() != before
