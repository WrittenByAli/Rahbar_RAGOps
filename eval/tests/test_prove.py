"""Tests for prove_release.py and prove_ingest.py."""

from __future__ import annotations

import json
from pathlib import Path

import prove_ingest
import prove_release

# ------------------------------------------------------------------ prove_release


def _trace(trace_id: str, collection: str, embed_model: str = "nomic-embed-text",
           release_id: str = "v1", embed_version: str = "abc123") -> str:
    return json.dumps({
        "kind": "trace",
        "schema": 1,
        "trace_id": trace_id,
        "ts": "2026-11-27T10:00:00.000Z",
        "query": "test query",
        "embedding": {"model": embed_model, "version": embed_version},
        "collection": {"name": collection, "version": "col-ver"},
        "release_id": release_id,
    })


def _write_traces(tmp_path: Path, records: list[str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "test.jsonl"
    f.write_text("\n".join(records) + "\n", encoding="utf-8")
    return tmp_path


class TestProveRelease:
    def test_valid_proof_is_written(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1", release_id="v1"),
            _trace("tid-released", "rahbar_v2", release_id="v2"),
            _trace("tid-after",    "rahbar_v1", release_id="v1"),
        ])
        out = tmp_path / "results" / "release"
        # Monkeypatch the output path
        orig = prove_release.RESULT_DIR
        prove_release.RESULT_DIR = out
        prove_release.OUT = out / "undo.json"
        try:
            rc = prove_release.build_proof(
                "tid-before", "tid-released", "tid-after", [str(traces_dir)])
        finally:
            prove_release.RESULT_DIR = orig
            prove_release.OUT = orig / "undo.json"
        assert rc == 0
        proof = json.loads((out / "undo.json").read_text(encoding="utf-8"))
        assert proof["before"]["collection"] == "rahbar_v1"
        assert proof["released"]["collection"] == "rahbar_v2"
        assert proof["after_undo"]["collection"] == "rahbar_v1"

    def test_missing_trace_id_fails(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1"),
            _trace("tid-released", "rahbar_v2"),
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-MISSING", [str(traces_dir)])
        assert rc != 0

    def test_same_collection_before_and_released_fails(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1"),
            _trace("tid-released", "rahbar_v1"),  # same — no rollout happened
            _trace("tid-after",    "rahbar_v1"),
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-after", [str(traces_dir)])
        assert rc != 0

    def test_after_collection_differs_from_before_fails(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1"),
            _trace("tid-released", "rahbar_v2"),
            _trace("tid-after",    "rahbar_v2"),  # undo didn't restore v1
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-after", [str(traces_dir)])
        assert rc != 0

    def test_embed_model_change_after_undo_fails(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1", embed_model="nomic-embed-text"),
            _trace("tid-released", "rahbar_v2", embed_model="mxbai-embed-large"),
            _trace("tid-after",    "rahbar_v1", embed_model="mxbai-embed-large"),
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-after", [str(traces_dir)])
        assert rc != 0

    def test_embed_version_change_after_undo_fails(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1", embed_version="v-old"),
            _trace("tid-released", "rahbar_v2", embed_version="v-new"),
            _trace("tid-after",    "rahbar_v1", embed_version="v-new"),
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-after", [str(traces_dir)])
        assert rc != 0

    def test_non_unique_trace_ids_fail(self, tmp_path: Path) -> None:
        traces_dir = _write_traces(tmp_path / "traces", [
            _trace("tid-before",   "rahbar_v1"),
            _trace("tid-released", "rahbar_v2"),
        ])
        rc = prove_release.build_proof(
            "tid-before", "tid-released", "tid-before", [str(traces_dir)])
        assert rc != 0


# ------------------------------------------------------------------ prove_ingest


def _verif_report(run_id: str = "r1", batch: str = "A",
                  decision: str = "accepted") -> dict:
    return {
        "run_id": run_id,
        "started_at": "2026-11-27T10:00:00Z",
        "batch": batch,
        "verify_embed_model": "nomic-embed-text",
        "verification_embedding_calls": 2,
        "documents": [{
            "file": "rules.pdf",
            "doc_id": "rules",
            "doc_version": "2026-06",
            "decision": decision,
            "checks": {
                "quality":      {"status": "pass", "reason": "text ok"},
                "completeness": {"status": "pass", "reason": "metadata ok"},
                "recency":      {"status": "pass", "reason": "no newer version"},
                "uniqueness":   {"status": "pass", "reason": "hash unique"},
            },
        }],
    }


def _quarantine_proof() -> dict:
    return {
        "documents": [
            {"file": "bad.pdf", "in_quarantine": True, "indexed_chunks": 0,
             "reason": "quality fail"},
        ],
    }


def _ingest_result(run_id: str = "r1", collection: str = "rahbar_v1") -> dict:
    return {
        "run_id": run_id,
        "collection": collection,
        "started_at": "2026-11-27T10:00:00Z",
        "counts": {"chunks_embedded": 10, "documents_unchanged": 0},
        "lake_state_id": "abc123",
    }


class TestProveIngest:
    def _write_src(self, src: Path, **dirs: list[dict]) -> None:
        for subdir, items in dirs.items():
            d = src / subdir
            d.mkdir(parents=True, exist_ok=True)
            for item in items:
                name = f"{item.get('run_id', 'test')}.json"
                (d / name).write_text(json.dumps(item), encoding="utf-8")

    def test_valid_verification_report_accepted(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        prove_ingest.DEST = dest
        self._write_src(src, verification=[_verif_report()])
        rc = prove_ingest.run(src)
        assert rc == 0
        assert (dest / "verification" / "r1.json").exists()

    def test_missing_run_id_rejected(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        prove_ingest.DEST = dest
        bad = _verif_report()
        del bad["run_id"]
        self._write_src(src, verification=[bad])
        rc = prove_ingest.run(src)
        assert rc != 0

    def test_quarantine_proof_accepted(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        prove_ingest.DEST = dest
        proof = _quarantine_proof()
        (src / "proofs").mkdir(parents=True, exist_ok=True)
        (src / "proofs" / "quarantine.json").write_text(
            json.dumps(proof), encoding="utf-8")
        rc = prove_ingest.run(src)
        assert rc == 0
        assert (dest / "proofs" / "quarantine.json").exists()

    def test_quarantine_with_indexed_chunks_rejected(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        prove_ingest.DEST = dest
        proof = {"documents": [{"file": "bad.pdf", "in_quarantine": True, "indexed_chunks": 3}]}
        (src / "proofs").mkdir(parents=True, exist_ok=True)
        (src / "proofs" / "quarantine.json").write_text(
            json.dumps(proof), encoding="utf-8")
        rc = prove_ingest.run(src)
        assert rc != 0

    def test_ingest_result_accepted(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        prove_ingest.DEST = dest
        self._write_src(src, ingest=[_ingest_result()])
        rc = prove_ingest.run(src)
        assert rc == 0


# ------------------------------------- validators mirroring harness/checks.py


def _changed_proof() -> dict:
    return {
        "run_id": "r1",
        "changed_doc_id": "beta",
        "collection": "rahbar_v1",
        "before": {"beta": ["b1", "b2"], "alpha": ["a1"]},
        "after":  {"beta": ["b3"],       "alpha": ["a1"]},
    }


def _restored_proof() -> dict:
    return {
        "run_id": "r1",
        "doc_id": "ug-rules",
        "restored_version": "2023-08",
        "replaced_version": "2026-06",
        "retrieved_versions_before": ["2026-06"],
        "retrieved_versions_after": ["2023-08"],
        "chunks_embedded": 0,
    }


class TestVerificationDecision:
    def test_accepted_with_all_passing_is_valid(self) -> None:
        assert prove_ingest.validate_verification(
            _verif_report(), Path("v.json")) == []

    def test_unknown_decision_rejected(self) -> None:
        errs = prove_ingest.validate_verification(
            _verif_report(decision="maybe"), Path("v.json"))
        assert any("not accepted/quarantined/unchanged" in e for e in errs)

    def test_quarantined_but_all_checks_pass_rejected(self) -> None:
        errs = prove_ingest.validate_verification(
            _verif_report(decision="quarantined"), Path("v.json"))
        assert any("contradicts" in e for e in errs)

    def test_accepted_with_a_failing_check_rejected(self) -> None:
        report = _verif_report()
        report["documents"][0]["checks"]["quality"] = {
            "status": "fail", "reason": "scanned, no text layer"}
        errs = prove_ingest.validate_verification(report, Path("v.json"))
        assert any("contradicts" in e for e in errs)


class TestChangedProof:
    def test_valid_changed_proof(self) -> None:
        assert prove_ingest.validate_proof(
            _changed_proof(), Path("changed.json")) == []

    def test_target_chunks_unchanged_rejected(self) -> None:
        proof = _changed_proof()
        proof["after"]["beta"] = ["b1", "b2"]
        errs = prove_ingest.validate_proof(proof, Path("changed.json"))
        assert any("unchanged" in e for e in errs)

    def test_no_other_document_rejected(self) -> None:
        proof = _changed_proof()
        del proof["before"]["alpha"]
        del proof["after"]["alpha"]
        errs = prove_ingest.validate_proof(proof, Path("changed.json"))
        assert any("at least one other document" in e for e in errs)

    def test_other_document_moved_rejected(self) -> None:
        proof = _changed_proof()
        proof["after"]["alpha"] = ["a9"]
        errs = prove_ingest.validate_proof(proof, Path("changed.json"))
        assert any("other documents changed too" in e for e in errs)


class TestDeletedAndRestoredProofs:
    def test_count_before_zero_rejected(self) -> None:
        proof = {"doc_id": "x", "count_before": 0,
                 "count_after": 0, "orphan_chunks_after": 0}
        errs = prove_ingest.validate_proof(proof, Path("deleted.json"))
        assert any("count_before" in e for e in errs)

    def test_valid_restored_proof(self) -> None:
        assert prove_ingest.validate_proof(
            _restored_proof(), Path("restored.json")) == []

    def test_before_versions_must_be_exactly_the_replaced_one(self) -> None:
        proof = _restored_proof()
        proof["retrieved_versions_before"] = ["2026-06", "2023-08"]
        errs = prove_ingest.validate_proof(proof, Path("restored.json"))
        assert any("retrieved_versions_before" in e for e in errs)

    def test_after_versions_must_be_exactly_the_restored_one(self) -> None:
        proof = _restored_proof()
        proof["retrieved_versions_after"] = []
        errs = prove_ingest.validate_proof(proof, Path("restored.json"))
        assert any("retrieved_versions_after" in e for e in errs)


class TestIngestZeroPairing:
    def test_missing_lake_state_id_rejected(self) -> None:
        run = _ingest_result()
        del run["lake_state_id"]
        errs = prove_ingest.validate_ingest(run, Path("i.json"))
        assert any("lake_state_id" in e for e in errs)

    def test_zero_embed_pair_found(self) -> None:
        first = _ingest_result(run_id="r1")
        second = {**_ingest_result(run_id="r2"),
                  "started_at": "2026-11-27T11:00:00Z",
                  "counts": {"chunks_embedded": 0, "documents_unchanged": 12}}
        assert prove_ingest._zero_embed_pair([first, second]) is True

    def test_zero_embed_pair_needs_same_lake_state(self) -> None:
        first = _ingest_result(run_id="r1")
        second = {**_ingest_result(run_id="r2"),
                  "started_at": "2026-11-27T11:00:00Z",
                  "lake_state_id": "different",
                  "counts": {"chunks_embedded": 0, "documents_unchanged": 12}}
        assert prove_ingest._zero_embed_pair([first, second]) is False
