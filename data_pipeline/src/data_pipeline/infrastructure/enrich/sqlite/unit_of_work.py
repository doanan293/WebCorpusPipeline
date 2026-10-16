from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from data_pipeline.domain.enrich.models import DownloadedFile, RunSummary
from data_pipeline.infrastructure.enrich.files.download_files import verify_file
from data_pipeline.infrastructure.enrich.sqlite.downloads import SqliteDownloads
from data_pipeline.infrastructure.enrich.sqlite.failures import SqliteFailures
from data_pipeline.infrastructure.enrich.sqlite.inventory import SqliteInventory
from data_pipeline.infrastructure.enrich.sqlite.parses import SqliteParses


class SqliteEnrichUnitOfWork:
    """One open work directory: ``state.sqlite3`` behind ``.writer.lock``, split into repositories.

    Every repository shares the one connection and the one write lock.
    """

    def __init__(self, root: Path, conn: sqlite3.Connection, lock_fd: int) -> None:
        self.root, self.conn = root, conn
        self._lock_fd: int | None = lock_fd
        self._connection_open = True
        # LlamaParse workers record their jobs from other threads; every write takes this lock.
        self._write_lock: AbstractContextManager[Any] = threading.RLock()
        self.inventory = SqliteInventory(conn, self._write_lock)
        self.downloads = SqliteDownloads(conn, self._write_lock, root)
        self.parses = SqliteParses(conn, self._write_lock)
        self.failures = SqliteFailures(conn, self._write_lock)

    @classmethod
    @contextmanager
    def open(
        cls,
        root: Path,
        crawl_id: str,
        *,
        rebind: bool = False,
    ) -> Iterator[SqliteEnrichUnitOfWork]:
        """Open the work directory of one crawl; ``rebind`` moves it to another crawl."""
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / ".writer.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl  # noqa: PLC0415 -- fcntl does not exist on Windows; locking is skipped there

            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, ImportError):
            os.close(lock_fd)
            msg = f"work directory is already in use: {root}"
            raise RuntimeError(msg) from None
        conn = sqlite3.connect(root / "state.sqlite3", timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        obj = cls(root, conn, lock_fd)
        obj._init_schema()
        bound = obj._meta("crawl_id")
        if bound is not None and bound != crawl_id and not rebind:
            obj.close()
            msg = (
                f"work directory belongs to crawl {bound}, not {crawl_id}; use a new work "
                "directory, or pass --rebind-source to switch it to this crawl"
            )
            raise ValueError(msg)
        obj._set_meta("crawl_id", crawl_id)
        try:
            yield obj
        finally:
            obj.close()

    def close(self) -> None:
        if self._connection_open:
            self.conn.commit()
            self.conn.close()
            self._connection_open = False
        if self._lock_fd is not None:
            try:
                import fcntl  # noqa: PLC0415 -- fcntl does not exist on Windows; locking is skipped there

                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def _init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory (
          url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, part_path TEXT, final_path TEXT,
          sha256 TEXT, size INTEGER, mime_type TEXT, final_url TEXT, etag TEXT,
          expected_size INTEGER, retry_at REAL, error TEXT, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refs (
          source_url TEXT NOT NULL, target_url TEXT NOT NULL, relation TEXT NOT NULL,
          anchor_text TEXT NOT NULL DEFAULT '', alt TEXT NOT NULL DEFAULT '',
          caption TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(source_url,target_url,relation,anchor_text,alt,caption)
        );
        CREATE INDEX IF NOT EXISTS refs_target ON refs(target_url);
        CREATE TABLE IF NOT EXISTS parse_results (
          url TEXT PRIMARY KEY, status TEXT NOT NULL, result_json TEXT, cache_path TEXT,
          used_cloud INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cloud_jobs (
          request_id TEXT PRIMARY KEY, url TEXT NOT NULL, file_id TEXT, job_id TEXT,
          key_label TEXT, key_fingerprint TEXT, status TEXT NOT NULL, payload TEXT,
          updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS failures (
          url TEXT NOT NULL, stage TEXT NOT NULL, reason TEXT NOT NULL,
          retry_at REAL, updated REAL NOT NULL, PRIMARY KEY(url,stage)
        );
        CREATE TABLE IF NOT EXISTS inventory_sources (
          path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
          cursor INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0
        );
        """)
        self.conn.commit()

    def _meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def recover(self) -> None:
        """Requeue downloads cut short; a finalizing row is done only if its file verifies."""
        now = time.time()
        with self._write_lock, self.conn:
            self.conn.execute(
                "UPDATE inventory SET status='pending',updated=? WHERE status='in_progress'", (now,)
            )
            rows = self.conn.execute("SELECT * FROM inventory WHERE status='finalizing'").fetchall()
            for row in rows:
                file = DownloadedFile(
                    row["url"],
                    Path(row["final_path"] or ""),
                    row["sha256"] or "",
                    row["size"] or 0,
                    row["mime_type"] or "application/octet-stream",
                    row["final_url"] or row["url"],
                )
                status = "downloaded" if row["final_path"] and verify_file(file) else "pending"
                self.conn.execute(
                    "UPDATE inventory SET status=?,updated=? WHERE url=?", (status, now, row["url"])
                )

    def summary(self) -> RunSummary:
        def n(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])

        return RunSummary(
            downloaded=n("SELECT count(*) FROM inventory WHERE status='downloaded'"),
            parsed=n("SELECT count(*) FROM parse_results WHERE status='extracted'"),
            failed=n("SELECT count(*) FROM failures WHERE retry_at IS NULL"),
            pending=n(
                "SELECT count(*) FROM inventory WHERE status IN "
                "('pending','retry','in_progress','finalizing')"
            ),
            quota_blocked=n("SELECT count(*) FROM parse_results WHERE status='quota_blocked'"),
            cloud_failed=n("SELECT count(*) FROM parse_results WHERE status='cloud_failed'"),
            transferred_bytes=n(
                "SELECT coalesce(sum(size),0) FROM inventory WHERE status='downloaded'"
            ),
        )


def read_cloud_job_status(work_dir: Path, request_id: str, job_id: str) -> str:
    """Return a cloud job's status without opening (and locking) the work state."""
    connection = sqlite3.connect(work_dir / "state.sqlite3")
    try:
        row = connection.execute(
            "SELECT job_id, status FROM cloud_jobs WHERE request_id=?", (request_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] != job_id:
        msg = "request/job pair is not present in this work directory"
        raise ValueError(msg)
    return str(row[1])
