"""One writer at a time: the ingestion lock (ARCHITECTURE 3.7).

The lock is a file in the data lake, created with O_EXCL, so only one process can create
it. While a run is alive, a background thread refreshes the file's modification time.

Stale-owner rule: a lock that has not been refreshed for `stale_seconds` belongs to a run
that was killed. The next run takes it over. So a dead pod blocks ingestion only for that
long, and a long run is never mistaken for a dead one.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Self


class LockHeld(Exception):
    """Another run holds the ingestion lock."""


class LockLost(Exception):
    """Another run took the lock over, so this one must stop writing."""


class IngestLock:
    def __init__(self, path: Path, stale_seconds: float) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._heartbeat = threading.Thread(target=self._beat, daemon=True)

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self._create():
            self._take_over_if_stale()
        self._heartbeat.start()
        return self

    def __exit__(self, *_error: object) -> None:
        self._stop.set()
        self._heartbeat.join()
        if _holder(self.path).get("token") == self.token:     # never remove another's lock
            self.path.unlink(missing_ok=True)

    def check(self) -> None:
        """Call before writing: raises if the lock is no longer ours."""
        if _holder(self.path).get("token") != self.token:
            raise LockLost(f"{self.path} was taken over by another run; stopping")

    def _create(self) -> bool:
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"token": self.token, "owner": socket.gethostname(), "pid": os.getpid(),
                       "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, file)
        return True

    def _take_over_if_stale(self) -> None:
        try:
            idle = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:                   # released just now
            if self._create():
                return
            raise LockHeld(f"{self.path} is held by another run") from None
        holder = _holder(self.path)
        if idle <= self.stale_seconds:
            raise LockHeld(f"{self.path} is held by {holder.get('owner', 'an unknown owner')} "
                           f"since {holder.get('acquired_at', 'an unknown time')} "
                           f"(last sign of life {idle:.0f}s ago, stale after "
                           f"{self.stale_seconds:.0f}s)")
        # Move the stale file aside first. Only one contender can move it.
        aside = self.path.with_name(f"{self.path.name}.stale-{self.token}")
        try:
            os.rename(self.path, aside)
        except FileNotFoundError:
            raise LockHeld(f"{self.path} is held by a run that is taking it over") from None
        if _holder(aside) != holder:                # a new lock appeared after we looked
            os.rename(aside, self.path)
            raise LockHeld(f"{self.path} is held by another run")
        aside.unlink()
        if not self._create():
            raise LockHeld(f"{self.path} is held by another run")

    def _beat(self) -> None:
        while not self._stop.wait(self.stale_seconds / 5):
            if _holder(self.path).get("token") != self.token:
                return
            try:
                os.utime(self.path)                 # never creates the file
            except FileNotFoundError:
                return


def _holder(path: Path) -> dict:
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return holder if isinstance(holder, dict) else {}
