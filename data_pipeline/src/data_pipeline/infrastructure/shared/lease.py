"""One writer at a time for a shared SQLite state file, kept by a lease row inside that file.

A lock file would not do: the services run in containers and ``flock`` does not cross every bind
mount, while SQLite's own locking already has to. A holder that dies stops renewing, so its lease
goes stale.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from data_pipeline.domain.shared.errors import StateLockedError
from data_pipeline.infrastructure.shared.sqlite import connect

_SCHEMA = "CREATE TABLE IF NOT EXISTS lease (name TEXT PRIMARY KEY, owner TEXT, heartbeat_at REAL)"
_NAME = "state"


class _Clock(Protocol):
    def now(self) -> float: ...


class SqliteLease:
    def __init__(
        self, path: Path, owner: str, clock: _Clock, *, ttl: float = 60.0, renew_every: float = 10.0
    ) -> None:
        self.path = path
        self.owner = owner
        self.clock = clock
        self.ttl = ttl
        self.renew_every = renew_every

    def _db(self) -> sqlite3.Connection:
        db = connect(self.path)
        db.execute(_SCHEMA)
        return db

    def acquire(self) -> None:
        """Take the lease, or raise ``StateLockedError`` naming the live holder."""
        db = self._db()
        try:
            now = self.clock.now()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner, heartbeat_at FROM lease WHERE name = ?", (_NAME,)
            ).fetchone()
            if row is not None and row[0] != self.owner and row[1] + self.ttl > now:
                db.execute("ROLLBACK")
                msg = f"{self.path} is in use by {row[0]}; stop it first"
                raise StateLockedError(msg)
            db.execute(
                "INSERT OR REPLACE INTO lease (name, owner, heartbeat_at) VALUES (?, ?, ?)",
                (_NAME, self.owner, now),
            )
            db.execute("COMMIT")
        finally:
            db.close()

    def renew(self) -> None:
        db = self._db()
        try:
            db.execute(
                "UPDATE lease SET heartbeat_at = ? WHERE name = ? AND owner = ?",
                (self.clock.now(), _NAME, self.owner),
            )
        finally:
            db.close()

    def release(self) -> None:
        db = self._db()
        try:
            db.execute("DELETE FROM lease WHERE name = ? AND owner = ?", (_NAME, self.owner))
        finally:
            db.close()

    @contextmanager
    def held(self) -> Iterator[None]:
        """Hold the lease for the block, renewing it from a background thread."""
        self.acquire()
        stop = threading.Event()

        def keep_alive() -> None:
            while not stop.wait(self.renew_every):
                self.renew()

        thread = threading.Thread(target=keep_alive, name="state-lease", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
            self.release()
