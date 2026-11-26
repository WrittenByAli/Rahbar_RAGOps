"""The incremental update (A2.1 to A2.4): embed only what changed, leave no orphans,
archive and restore through metadata."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from fakes import FakeClient, FakeEmbedder, prose, put_markdown, services, settings
from pipeline.admin import count_chunks, delete_document, restore_version, status
from pipeline.ingest import EmptyLake, run_ingest
from pipeline.sync import ForeignCollection
from pipeline.store import ModelMismatch


def sections(seed: int, count: int = 4) -> str:
    """A document with `count` headed sections, so an edit touches only some chunks."""
    return "\n\n".join(f"## Part {n}\n{prose(seed * 100 + n, 60)}" for n in range(1, count + 1))


def ids_by_doc(client: FakeClient, name: str = "rahbar") -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for chunk_id, row in client.collections[name].rows.items():
        found[row["metadata"]["doc_id"]].append(chunk_id)
    return {doc: sorted(ids) for doc, ids in found.items()}


def retrievable_versions(client: FakeClient, doc_id: str, name: str = "rahbar") -> list[str]:
    """What the API would retrieve: its filter is where={"superseded": False}."""
    got = client.collections[name].get(
        where={"$and": [{"doc_id": doc_id}, {"superseded": False}]}, include=["metadatas"])
    return sorted({m["doc_version"] for m in got["metadatas"]})


def seed_corpus(config) -> None:  # type: ignore[no-untyped-def]
    put_markdown(config.lake_dir, "alpha.md", sections(1))
    put_markdown(config.lake_dir, "beta.md", sections(2))
    put_markdown(config.lake_dir, "gamma.md", sections(3))


def proof(config, name: str) -> dict:  # type: ignore[no-untyped-def]
    return json.loads((Path(config.report_dir) / "proofs" / name).read_text("utf-8"))


# ---------------------------------------------------------------- A2.1
def test_unchanged_corpus_embeds_zero_chunks(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    seed_corpus(config)
    first = run_ingest(config, services(client, embedder)).ingest[0]
    collection = client.collections["rahbar"]
    assert first["counts"]["chunks_embedded"] == collection.count() > 0
    assert first["counts"]["documents_new"] == 3
    embedder.reset()
    collection.writes.clear()

    second = run_ingest(config, services(client, embedder)).ingest[0]

    assert second["counts"]["chunks_embedded"] == 0
    assert second["counts"]["chunks_upserted"] == 0
    assert second["counts"]["chunks_deleted"] == 0
    assert second["counts"]["documents_scanned"] == 3
    assert second["counts"]["documents_unchanged"] == 3
    assert second["counts"]["verification_embedding_calls"] == 0
    assert embedder.calls == []                          # no embedding of any kind
    assert collection.writes == []                       # and not even a new generation
    assert second["lake_state_id"] == first["lake_state_id"]
    reports = sorted((Path(config.report_dir) / "ingest").glob("*.json"))
    assert [json.loads(p.read_text("utf-8"))["run_id"] for p in reports] == [
        first["run_id"], second["run_id"]]


def test_unchanged_read_only_source_embeds_zero_chunks(tmp_path: Path) -> None:
    """CORPUS_DIR mode (CI): files are only read, and a second run changes nothing."""
    source = tmp_path / "corpus"
    for seed, name in ((1, "alpha.md"), (2, "beta.md")):
        put_markdown(tmp_path / "scratch", name, sections(seed))
        source.mkdir(exist_ok=True)
        (tmp_path / "scratch" / "inbox" / "A" / name).rename(source / name)
    (source / "README.md").write_text("not a document", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    config = settings(tmp_path, corpus_dir=str(source))
    client, embedder = FakeClient(), FakeEmbedder()
    run_ingest(config, services(client, embedder))
    embedder.reset()

    second = run_ingest(config, services(client, embedder))

    assert {e["decision"] for e in second.verification["documents"]} == {"unchanged"}
    assert len(second.verification["documents"]) == 2
    assert second.ingest[0]["counts"]["chunks_embedded"] == 0
    assert embedder.calls == []
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


# ---------------------------------------------------------------- A2.2
def test_changed_document_replaces_only_its_chunks(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    seed_corpus(config)
    run_ingest(config, services(client, embedder))
    before = ids_by_doc(client)
    vectors_before = {i: row["embedding"] for i, row in client.collections["rahbar"].rows.items()}
    embedder.reset()

    changed = sections(2).replace("## Part 3\n", "## Part 3\nAttendance is now compulsory. ")
    put_markdown(config.lake_dir, "beta.md", changed)
    result = run_ingest(config, services(client, embedder), prove_changed="beta")
    after = ids_by_doc(client)

    assert after["alpha"] == before["alpha"] and after["gamma"] == before["gamma"]
    assert after["beta"] != before["beta"]
    new_ids = set(after["beta"]) - set(before["beta"])
    gone_ids = set(before["beta"]) - set(after["beta"])
    kept_ids = set(after["beta"]) & set(before["beta"])
    assert new_ids and gone_ids and kept_ids             # only the edited part was replaced
    counts = result.ingest[0]["counts"]
    assert counts["chunks_embedded"] == len(new_ids)
    assert counts["chunks_deleted"] == len(gone_ids)
    assert counts["documents_changed"] == 1 and counts["documents_unchanged"] == 2
    rows = client.collections["rahbar"].rows
    assert all(rows[i]["embedding"] == vectors_before[i] for i in kept_ids)
    release_texts = [t for model, texts in embedder.calls for t in texts
                     if model == config.embed_model and "Part" in t]
    assert len(release_texts) >= 1
    written = proof(config, "changed.json")
    assert written["changed_doc_id"] == "beta"
    assert written["before"] == before and written["after"] == after


def test_changed_proof_is_refused_when_the_named_document_did_not_change(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))

    result = run_ingest(config, services(client), prove_changed="beta")

    assert result.problems and "did not change" in result.problems[0]


def test_changed_fingerprint_reembeds_every_chunk_of_the_document(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    seed_corpus(config)
    run_ingest(config, services(client, embedder))
    total = client.collections["rahbar"].count()
    embedder.reset()
    new_model_files = services(client, embedder)
    new_model_files.model_digest = lambda model: f"new-digest-of-{model}"

    counts = run_ingest(config, new_model_files).ingest[0]["counts"]

    assert counts["chunks_embedded"] == total
    assert counts["documents_changed"] == 3


def test_new_chunk_size_replaces_the_old_chunks_without_orphans(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    old = set(client.collections["rahbar"].rows)

    counts = run_ingest(settings(tmp_path, chunk_size=15), services(client)).ingest[0]["counts"]

    rows = client.collections["rahbar"].rows
    assert counts["chunks_embedded"] == len(rows) > len(old)
    assert len({r["metadata"]["fingerprint"] for r in rows.values()}) == 1


def test_metadata_only_difference_is_updated_without_embedding(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    seed_corpus(config)
    run_ingest(config, services(client, embedder))
    collection = client.collections["rahbar"]
    victim = sorted(collection.rows)[0]
    collection.rows[victim]["metadata"]["page_start"] = 99
    embedder.reset()

    counts = run_ingest(config, services(client, embedder)).ingest[0]["counts"]

    assert collection.rows[victim]["metadata"]["page_start"] == 1
    assert counts["chunks_embedded"] == 0 and embedder.calls == []


def test_chunk_metadata_describes_itself(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    rows = client.collections["rahbar"].rows

    per_doc = ids_by_doc(client)
    for row in rows.values():
        meta = row["metadata"]
        assert set(meta) == {"doc_id", "doc_version", "content_sha256", "fingerprint", "source",
                             "section", "page", "page_start", "page_end", "superseded",
                             "status", "chunk_count"}
        assert meta["chunk_count"] == len(per_doc[meta["doc_id"]])
        assert meta["status"] == "active" and meta["superseded"] is False
        assert len(meta["content_sha256"]) == 64


# ---------------------------------------------------------------- index_generation
def test_generation_is_bumped_before_the_first_write_and_after_the_last(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.writes.clear()
    collection.generations.clear()

    put_markdown(config.lake_dir, "delta.md", sections(4))
    run_ingest(config, services(client))

    assert collection.writes[0] == "modify" and collection.writes[-1] == "modify"
    assert len(collection.writes) >= 3
    assert len(set(collection.generations)) == 2
    assert collection.metadata["embed_model"] == config.embed_model     # modify() replaces all
    assert collection.metadata["write_in_progress"] is False
    assert "hnsw:space" not in collection.metadata


def test_run_after_a_crash_bumps_the_generation_even_with_nothing_to_write(
        tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    crashed = dict(collection.metadata, write_in_progress=True)     # died before the last bump
    collection.modify(crashed)
    collection.generations.clear()

    run_ingest(config, services(client))

    assert collection.generations and collection.generations[-1] != crashed["index_generation"]
    assert collection.metadata["write_in_progress"] is False


def test_interrupted_run_repairs_itself(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "alpha.md", sections(1))
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    real_delete = collection.delete

    def die(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConnectionError("pod killed")

    put_markdown(config.lake_dir, "alpha.md", sections(9))
    monkeypatch.setattr(collection, "delete", die)
    with pytest.raises(ConnectionError):
        run_ingest(config, services(client))
    monkeypatch.setattr(collection, "delete", real_delete)

    counts = run_ingest(config, services(client)).ingest[0]["counts"]

    assert counts["chunks_embedded"] == 0               # the new chunks were already there
    assert counts["chunks_deleted"] > 0                 # the stale ones go now
    assert {r["metadata"]["chunk_count"] for r in collection.rows.values()} == {collection.count()}
    assert collection.metadata["write_in_progress"] is False


# ---------------------------------------------------------------- A2.3
def test_deleted_document_leaves_no_orphans(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    put_markdown(config.lake_dir, "beta-old.md", sections(8), doc_id="beta",
                 doc_version="2024-01")
    run_ingest(config, services(client))             # beta has an active and an archived version
    before = ids_by_doc(client)
    versions = {r["metadata"]["doc_version"] for r in client.collections["rahbar"].rows.values()
                if r["metadata"]["doc_id"] == "beta"}
    assert len(versions) == 2
    generation = client.collections["rahbar"].metadata["index_generation"]

    result = delete_document(config, services(client), "beta")

    after = ids_by_doc(client)
    assert "beta" not in after
    assert {k: v for k, v in before.items() if k != "beta"} == after
    assert count_chunks(config, services(client), "beta")["rahbar"]["total"] == 0
    written = proof(config, "deleted.json")
    assert written == result
    assert (written["doc_id"], written["collection"]) == ("beta", "rahbar")
    assert written["count_before"] == len(before["beta"]) > 0
    assert written["count_after"] == 0 and written["orphan_chunks_after"] == 0
    assert client.collections["rahbar"].metadata["index_generation"] != generation
    lake = Path(config.lake_dir)
    assert not (lake / "lake" / "beta").exists() or not list((lake / "lake" / "beta").iterdir())
    assert (lake / "archive" / "beta").is_dir()
    # The next run must not bring it back.
    counts = run_ingest(config, services(client)).ingest[0]["counts"]
    assert "beta" not in ids_by_doc(client)
    assert counts["chunks_embedded"] == 0 and counts["orphan_chunks"] == 0


def test_delete_of_an_unknown_document_fails(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))

    with pytest.raises(LookupError):
        delete_document(config, services(client), "no-such-doc")


def test_a_missing_inbox_file_never_deletes_anything(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))             # the inbox is empty afterwards
    before = ids_by_doc(client)

    counts = run_ingest(config, services(client)).ingest[0]["counts"]

    assert ids_by_doc(client) == before
    assert counts["documents_deleted"] == 0


def test_sweep_removes_chunks_that_no_lake_version_owns(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]
    collection.upsert(ids=["stray", "legacy"], embeddings=[[1.0], [1.0]],
                      documents=["x", "y"],
                      metadatas=[{"doc_id": "ghost", "doc_version": "2001"},
                                 {"source": "a2-chunk-without-doc-id"}])

    counts = run_ingest(config, services(client)).ingest[0]["counts"]

    assert counts["orphan_chunks"] == 2
    assert "stray" not in collection.rows and "legacy" not in collection.rows


def test_another_lake_cannot_sweep_this_lakes_chunks(tmp_path: Path) -> None:
    """A new or misconfigured lake must not wipe a collection another lake maintains."""
    client = FakeClient()
    config = settings(tmp_path)
    seed_corpus(config)
    run_ingest(config, services(client))
    before = ids_by_doc(client)
    other = settings(tmp_path, lake_dir=str(tmp_path / "other-lake"))
    put_markdown(other.lake_dir, "demo.md", sections(5))

    with pytest.raises(ForeignCollection):
        run_ingest(other, services(client))
    assert ids_by_doc(client) == before

    run_ingest(other, services(client), adopt=True)
    assert list(ids_by_doc(client)) == ["demo"]


def test_empty_lake_never_touches_the_collection(tmp_path: Path) -> None:
    client = FakeClient()
    config = settings(tmp_path)
    with pytest.raises(EmptyLake):
        run_ingest(config, services(client))
    assert client.collections == {}


def test_dummy_collection_is_replaced_by_the_real_index(tmp_path: Path) -> None:
    from pipeline.dummy import write_dummy_index

    client = FakeClient()
    config = settings(tmp_path)
    write_dummy_index(config, client)
    seed_corpus(config)

    run_ingest(config, services(client))

    collection = client.collections["rahbar"]
    assert collection.metadata["embed_model"] == config.embed_model
    assert not [i for i in collection.rows if i.startswith("dummy-")]


def test_collection_of_another_model_is_never_overwritten(tmp_path: Path) -> None:
    client = FakeClient()
    config = settings(tmp_path)
    seed_corpus(config)
    run_ingest(config, services(client))
    before = ids_by_doc(client)

    with pytest.raises(ModelMismatch):
        run_ingest(settings(tmp_path, embed_model="mxbai-embed-large"), services(client))

    assert ids_by_doc(client) == before


# ---------------------------------------------------------------- several targets
TARGETS = """
- {collection: rahbar-v1, embed_model: nomic-embed-text, chunk_size: 40, chunk_overlap: 5}
- {collection: rahbar-v2, embed_model: mxbai-embed-large, chunk_size: 25, chunk_overlap: 0}
"""


def test_every_target_collection_is_kept_up_to_date(tmp_path: Path) -> None:
    config = settings(tmp_path, index_targets=TARGETS)
    client, embedder = FakeClient(), FakeEmbedder()
    seed_corpus(config)

    reports = run_ingest(config, services(client, embedder)).ingest

    assert [r["collection"] for r in reports] == ["rahbar-v1", "rahbar-v2"]
    assert client.collections["rahbar-v2"].metadata["embed_model"] == "mxbai-embed-large"
    assert "rahbar" not in client.collections
    delete_document(config, services(client), "beta")
    for name in ("rahbar-v1", "rahbar-v2"):
        assert "beta" not in ids_by_doc(client, name)
    assert [t["collection"] for t in proof(config, "deleted.json")["targets"]] == [
        "rahbar-v1", "rahbar-v2"]


@pytest.mark.parametrize("bad", [
    "- {collection: a}",
    "[]",
    "- {collection: a, embed_model: m, chunk_size: 40, chunk_overlap: 5}\n"
    "- {collection: a, embed_model: m, chunk_size: 40, chunk_overlap: 5}",
])
def test_incomplete_or_repeated_targets_are_refused(tmp_path: Path, bad: str) -> None:
    from pipeline.config import targets

    with pytest.raises(ValueError):
        targets(settings(tmp_path, index_targets=bad))


# ---------------------------------------------------------------- A2.4
def two_versions(config, client: FakeClient, embedder: FakeEmbedder):  # type: ignore[no-untyped-def]
    old, new = sections(1), sections(1).replace("## Part 2\n", "## Part 2\nRevised in 2026. ")
    put_markdown(config.lake_dir, "rules-2023.md", old, doc_id="rules", doc_version="2023-08")
    put_markdown(config.lake_dir, "other.md", sections(3))
    run_ingest(config, services(client, embedder))
    put_markdown(config.lake_dir, "rules-2026.md", new, doc_id="rules", doc_version="2026-06")
    return run_ingest(config, services(client, embedder))


def test_superseded_excluded_by_metadata(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()

    result = two_versions(config, client, embedder)

    assert retrievable_versions(client, "rules") == ["2026-06"]
    rows = [r["metadata"] for r in client.collections["rahbar"].rows.values()
            if r["metadata"]["doc_id"] == "rules"]
    old = [m for m in rows if m["doc_version"] == "2023-08"]
    assert old, "the superseded version stays in the collection"
    assert {(m["superseded"], m["status"]) for m in old} == {(True, "superseded")}
    assert result.ingest[0]["counts"]["documents_superseded"] == 1
    assert (Path(config.lake_dir) / "archive" / "rules" / "2023-08").is_dir()


def test_restore_embeds_nothing_and_survives_next_run(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    two_versions(config, client, embedder)
    collection = client.collections["rahbar"]
    vectors = {i: row["embedding"] for i, row in collection.rows.items()}
    generation = collection.metadata["index_generation"]
    embedder.reset()

    result = restore_version(config, services(client, embedder), "rules", "2023-08")

    assert embedder.calls == []
    assert retrievable_versions(client, "rules") == ["2023-08"]
    assert {i: row["embedding"] for i, row in collection.rows.items()} == vectors
    assert collection.metadata["index_generation"] != generation
    assert proof(config, "restored.json") == result
    assert result["restored_version"] == "2023-08"
    assert result["replaced_version"] == "2026-06"
    assert result["retrieved_versions_before"] == ["2026-06"]
    assert result["retrieved_versions_after"] == ["2023-08"]
    assert result["chunks_embedded"] == 0
    assert (Path(config.lake_dir) / "lake" / "rules" / "2023-08").is_dir()
    assert (Path(config.lake_dir) / "archive" / "rules" / "2026-06").is_dir()

    # The scheduled run, and the same newer file delivered again, must not undo it.
    run_ingest(config, services(client, embedder))
    assert retrievable_versions(client, "rules") == ["2023-08"]
    newer = sections(1).replace("## Part 2\n", "## Part 2\nRevised in 2026. ")
    put_markdown(config.lake_dir, "rules-2026.md", newer, doc_id="rules", doc_version="2026-06")
    again = run_ingest(config, services(client, embedder))
    assert [e["decision"] for e in again.verification["documents"]] == ["unchanged"]
    assert retrievable_versions(client, "rules") == ["2023-08"]
    assert again.ingest[0]["counts"]["chunks_embedded"] == 0
    assert embedder.calls == []
    assert status(config, services(client))["documents"]["rules"]["pinned"] == "2023-08"


def test_correcting_the_archived_newer_version_does_not_undo_a_restore(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    two_versions(config, client, embedder)
    restore_version(config, services(client, embedder), "rules", "2023-08")

    put_markdown(config.lake_dir, "rules-2026.md", sections(6), doc_id="rules",
                 doc_version="2026-06")
    run_ingest(config, services(client, embedder))

    assert retrievable_versions(client, "rules") == ["2023-08"]


def test_a_version_never_seen_before_ends_the_pin(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    two_versions(config, client, embedder)
    restore_version(config, services(client, embedder), "rules", "2023-08")

    put_markdown(config.lake_dir, "rules-2027.md", sections(7), doc_id="rules",
                 doc_version="2027-01")
    run_ingest(config, services(client, embedder))

    assert retrievable_versions(client, "rules") == ["2027-01"]
    assert status(config, services(client))["documents"]["rules"]["pinned"] is None


@pytest.mark.parametrize("doc_id, version", [("rules", "2026-06"), ("rules", "1999"),
                                             ("nothing", "2023-08")])
def test_restore_refuses_what_it_cannot_restore(tmp_path: Path, doc_id: str,
                                                version: str) -> None:
    config = settings(tmp_path)
    client, embedder = FakeClient(), FakeEmbedder()
    two_versions(config, client, embedder)

    with pytest.raises(LookupError):
        restore_version(config, services(client, embedder), doc_id, version)


# ---------------------------------------------------------------- extraction cache
def test_text_is_extracted_once_per_file(tmp_path: Path, monkeypatch) -> None:
    from pipeline import ingest as ingest_module

    config = settings(tmp_path)
    client = FakeClient()
    seen: list[str] = []
    real_extract = ingest_module.extract

    def spy(path: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(Path(path).name)
        return real_extract(path, *args, **kwargs)

    monkeypatch.setattr(ingest_module, "extract", spy)
    for _ in range(3):
        put_markdown(config.lake_dir, "alpha.md", sections(1))
        run_ingest(config, services(client))

    assert seen == ["alpha.md"]


# ---------------------------------------------------------------- a new extractor
def test_extractor_version_is_part_of_the_fingerprint(monkeypatch) -> None:
    from pipeline import sync
    from pipeline.config import Target

    target = Target("rahbar", "nomic-embed-text", 400, 50)
    before = sync.fingerprint(target, "digest")
    monkeypatch.setattr(sync, "EXTRACTOR_VERSION", "next")

    assert sync.fingerprint(target, "digest") != before


def stale_extraction(config, doc_id: str, text: str) -> Path:  # type: ignore[no-untyped-def]
    """Make the lake copy look as if an older extractor had produced `text`."""
    lake = Path(config.lake_dir)
    manifest_path = lake / "state" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    for entry in manifest["documents"][doc_id]["versions"].values():
        entry["extractor_version"] = "old"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    [extracted] = (lake / "lake" / doc_id).glob("*/extracted.json")
    pages = json.loads(extracted.read_text("utf-8"))
    pages[0]["text"] = text
    extracted.write_text(json.dumps(pages), encoding="utf-8")
    return extracted


def test_lake_text_is_extracted_again_when_the_extractor_changes(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    good = ids_by_doc(client)
    for cached in (Path(config.lake_dir) / "state" / "extract-cache").glob("*"):
        cached.unlink()
    extracted = stale_extraction(config, "beta", "text the old extractor garbled")

    run_ingest(config, services(client))

    assert "garbled" not in extracted.read_text("utf-8")
    assert ids_by_doc(client) == good


def test_a_failed_second_extraction_keeps_the_old_lake_text(tmp_path: Path,
                                                            monkeypatch) -> None:
    from pipeline import ingest as ingest_module

    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    run_ingest(config, services(client))
    extracted = stale_extraction(config, "beta", "old but readable text of beta")

    def broken(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("parser broke")

    monkeypatch.setattr(ingest_module, "extract", broken)
    for cached in (Path(config.lake_dir) / "state" / "extract-cache").glob("*"):
        cached.unlink()                 # otherwise the cached text is used, not extract

    run_ingest(config, services(client))

    assert "old but readable text of beta" in extracted.read_text("utf-8")
    manifest = json.loads(
        (Path(config.lake_dir) / "state" / "manifest.json").read_text("utf-8"))
    [entry] = manifest["documents"]["beta"]["versions"].values()
    assert entry["extractor_version"] == "old"          # tried again next time


# ---------------------------------------------------------------- lake_state_id
def stamps(client: FakeClient) -> dict[str, str | None]:
    return {name: c.metadata.get("lake_state_id") for name, c in client.collections.items()}


def test_every_target_records_the_lake_state_it_was_built_from(tmp_path: Path) -> None:
    config = settings(tmp_path, index_targets=TARGETS)
    client, embedder = FakeClient(), FakeEmbedder()

    def all_report(state: str) -> None:
        assert stamps(client) == {"rahbar-v1": state, "rahbar-v2": state}

    def current() -> str:
        return status(config, services(client))["lake_state_id"]

    reports = two_versions(config, client, embedder).ingest
    all_report(reports[0]["lake_state_id"])
    assert reports[0]["lake_state_id"] == reports[1]["lake_state_id"]
    superseded_state = current()

    restore_version(config, services(client, embedder), "rules", "2023-08")
    assert current() != superseded_state
    all_report(current())
    delete_document(config, services(client), "other")
    all_report(current())
    for collection in client.collections.values():
        assert collection.metadata["embed_model"] and collection.metadata["lake_id"]
        assert "hnsw:space" not in collection.metadata

    for collection in client.collections.values():
        collection.writes.clear()
    run_ingest(config, services(client, embedder))
    assert [c.writes for c in client.collections.values()] == [[], []]


def test_a_missing_stamp_is_added_without_a_new_generation(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    seed_corpus(config)
    state = run_ingest(config, services(client)).ingest[0]["lake_state_id"]
    collection = client.collections["rahbar"]
    collection.modify({k: v for k, v in collection.metadata.items() if k != "lake_state_id"})
    generation = collection.metadata["index_generation"]

    run_ingest(config, services(client))

    assert collection.metadata["lake_state_id"] == state
    assert collection.metadata["index_generation"] == generation    # answers did not change


@pytest.mark.parametrize("command", ["delete", "restore"])
def test_a_target_that_was_behind_does_not_claim_the_new_state(tmp_path: Path,
                                                               command: str) -> None:
    """delete and restore touch one document. A collection that did not already match
    the lake must not claim to match it afterwards."""
    config = settings(tmp_path, index_targets=TARGETS)
    client, embedder = FakeClient(), FakeEmbedder()
    two_versions(config, client, embedder)
    behind = client.collections["rahbar-v2"]
    behind.modify(dict(behind.metadata, lake_state_id="an-older-state"))

    if command == "delete":
        delete_document(config, services(client), "rules")
    else:
        restore_version(config, services(client, embedder), "rules", "2023-08")

    state = status(config, services(client))["lake_state_id"]
    assert stamps(client) == {"rahbar-v1": state, "rahbar-v2": None}
    run_ingest(config, services(client, embedder))      # a full reconcile may claim it
    assert stamps(client) == {"rahbar-v1": state, "rahbar-v2": state}


def test_an_interrupted_run_leaves_no_lake_state_behind(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "alpha.md", sections(1))
    run_ingest(config, services(client))
    collection = client.collections["rahbar"]

    def die(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ConnectionError("pod killed")

    put_markdown(config.lake_dir, "alpha.md", sections(9))
    monkeypatch.setattr(collection, "delete", die)
    with pytest.raises(ConnectionError):
        run_ingest(config, services(client))

    assert "lake_state_id" not in collection.metadata
    assert collection.metadata["write_in_progress"] is True
