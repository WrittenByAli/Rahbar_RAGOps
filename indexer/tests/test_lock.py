"""One writer at a time (F2.2): the ingestion lock and its stale-owner rule."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fakes import FakeClient, prose, put_markdown, services, settings
from pipeline import run as cli
from pipeline.admin import delete_document, restore_version
from pipeline.ingest import run_ingest
from pipeline.lock import IngestLock, LockHeld, LockLost


def age(path: Path, seconds: float) -> None:
    """Make the lock look as if its owner last gave a sign of life `seconds` ago."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_second_concurrent_ingest_is_refused(tmp_path: Path) -> None:
    config = settings(tmp_path)
    client = FakeClient()
    put_markdown(config.lake_dir, "alpha.md", prose(1))
    lock_path = Path(config.lake_dir) / "state" / "ingest.lock"

    with IngestLock(lock_path, stale_seconds=300):
        with pytest.raises(LockHeld) as refused:
            run_ingest(config, services(client))

    assert "held by" in str(refused.value)
    assert client.collections == {}                              # nothing was written
    assert (Path(config.lake_dir) / "inbox" / "A" / "alpha.md").exists()
    assert not (Path(config.lake_dir) / "state" / "manifest.json").exists()
    run_ingest(config, services(client))                         # free again afterwards
    assert client.collections["rahbar"].count() > 0
    assert not lock_path.exists()


@pytest.mark.parametrize("command", ["delete", "restore"])
def test_delete_and_restore_take_the_same_lock(tmp_path: Path, command: str) -> None:
    config = settings(tmp_path)
    with IngestLock(Path(config.lake_dir) / "state" / "ingest.lock", stale_seconds=300):
        with pytest.raises(LockHeld):
            if command == "delete":
                delete_document(config, services(), "x")
            else:
                restore_version(config, services(), "x", "2026")


def test_command_line_exits_non_zero_when_the_lock_is_held(tmp_path: Path, monkeypatch,
                                                           capsys) -> None:
    config = settings(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: config)
    monkeypatch.setattr(cli, "real_services", lambda _settings: services())
    put_markdown(config.lake_dir, "alpha.md", prose(1))

    with IngestLock(Path(config.lake_dir) / "state" / "ingest.lock", stale_seconds=300):
        code = cli.main(["ingest"])

    assert code == cli.EXIT_LOCKED != 0
    assert "held by" in capsys.readouterr().out


def test_lock_of_a_dead_owner_is_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "ingest.lock"
    path.write_text(json.dumps({"token": "dead", "owner": "killed-pod"}), encoding="utf-8")
    age(path, 301)

    with IngestLock(path, stale_seconds=300):
        assert json.loads(path.read_text("utf-8"))["token"] != "dead"

    assert not path.exists()
    assert not list(tmp_path.glob("ingest.lock*"))              # no leftovers


def test_lock_with_a_recent_heartbeat_is_not_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "ingest.lock"
    path.write_text(json.dumps({"token": "alive", "owner": "other-pod"}), encoding="utf-8")
    age(path, 200)

    with pytest.raises(LockHeld, match="other-pod"):
        IngestLock(path, stale_seconds=300).__enter__()

    assert json.loads(path.read_text("utf-8"))["token"] == "alive"


def test_unreadable_stale_lock_does_not_block_forever(tmp_path: Path) -> None:
    path = tmp_path / "ingest.lock"
    path.write_text("", encoding="utf-8")                       # killed while creating it
    age(path, 400)

    with IngestLock(path, stale_seconds=300):
        pass


def test_heartbeat_keeps_a_long_run_alive(tmp_path: Path) -> None:
    path = tmp_path / "ingest.lock"
    with IngestLock(path, stale_seconds=1.0):
        time.sleep(1.6)                                          # longer than the limit
        assert time.time() - path.stat().st_mtime < 1.0
        with pytest.raises(LockHeld):
            IngestLock(path, stale_seconds=1.0).__enter__()


def test_owner_notices_when_its_lock_was_taken(tmp_path: Path) -> None:
    path = tmp_path / "ingest.lock"
    with IngestLock(path, stale_seconds=300) as lock:
        lock.check()
        path.write_text(json.dumps({"token": "someone-else"}), encoding="utf-8")
        with pytest.raises(LockLost):
            lock.check()

    assert path.exists(), "an owner must not remove a lock that is no longer its own"
