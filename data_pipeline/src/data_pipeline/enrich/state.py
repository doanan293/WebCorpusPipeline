from __future__ import annotations

import functools
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Concatenate

from data_pipeline.enrich.download.artifacts import verify_file
from data_pipeline.enrich.models import (
    DownloadedFile,
    DownloadJob,
    ParseResult,
    RunSummary,
    SourceProgress,
    SourceRef,
    jsonable,
)
from data_pipeline.enrich.parsing.serializers import parse_result_from_dict


def read_crawl_id(source: Path) -> str | None:
    folder = source if source.is_dir() else source.parent
    for name in ("README.json", "summary.json"):
        path = folder / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("crawl_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if value:
            return str(value)
    return None


def _locked[**P, R](
    method: Callable[Concatenate[WorkState, P], R],
) -> Callable[Concatenate[WorkState, P], R]:
    """Run a write method while holding the state's write lock."""

    @functools.wraps(method)
    def wrapper(self: WorkState, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._write_lock:
            return method(self, *args, **kwargs)

    return wrapper


class WorkState:
    def __init__(self, root: Path, conn: sqlite3.Connection, lock_fd: int):
        self.root, self.conn = root, conn
        self._lock_fd: int | None = lock_fd
        self._connection_open = True
        # LlamaParse workers record their jobs from other threads; every write takes this lock.
        self._write_lock: AbstractContextManager[Any] = threading.RLock()

    @classmethod
    @contextmanager
    def open(
        cls,
        root: Path,
        crawl_id: str,
        *,
        rebind: bool = False,
    ) -> Iterator[WorkState]:
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

    def source_progress(self, source_key: str) -> SourceProgress | None:
        row = self.conn.execute(
            "SELECT size, mtime_ns, cursor, done FROM inventory_sources WHERE path=?",
            (source_key,),
        ).fetchone()
        if row is None:
            return None
        return SourceProgress(source_key, int(row[0]), int(row[1]), int(row[2]), bool(row[3]))

    @_locked
    def add_inventory_batch(
        self, candidates: list[str], refs: list[SourceRef], progress: SourceProgress
    ) -> None:
        now = time.time()
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO inventory(url,updated) VALUES(?,?)",
                [(url, now) for url in candidates],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO "
                "refs(source_url,target_url,relation,anchor_text,alt,caption) VALUES(?,?,?,?,?,?)",
                [
                    (r.source_url, r.target_url, r.relation, r.anchor_text, r.alt, r.caption)
                    for r in refs
                ],
            )
            self.conn.execute(
                "INSERT INTO inventory_sources(path,size,mtime_ns,cursor,done) VALUES(?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,"
                "cursor=excluded.cursor,done=excluded.done",
                (
                    progress.path,
                    progress.size,
                    progress.mtime_ns,
                    progress.cursor,
                    int(progress.done),
                ),
            )

    def inventory_done(self, source_key: str) -> bool:
        """True once the source file was read to the end and has not changed since."""
        progress = self.source_progress(source_key)
        if progress is None or not progress.done:
            return False
        path = Path(source_key)
        if not path.exists():
            return False
        stat = path.stat()
        return (progress.size, progress.mtime_ns) == (stat.st_size, stat.st_mtime_ns)

    @_locked
    def delete_refs_from(self, source_urls: Iterable[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "DELETE FROM refs WHERE source_url=?", [(url,) for url in source_urls]
            )

    @_locked
    def claim_download(self) -> DownloadJob | None:
        now = time.time()
        row = self.conn.execute(
            "SELECT * FROM inventory WHERE status IN ('pending','retry') AND (retry_at IS NULL "
            "OR retry_at<=?) ORDER BY url LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE inventory SET status='in_progress',attempts=attempts+1,updated=? WHERE url=?",
            (now, row["url"]),
        )
        self.conn.commit()
        default_part = (
            self.root / "parts" / (hashlib.sha256(row["url"].encode()).hexdigest() + ".part")
        )
        return DownloadJob(
            row["url"],
            Path(row["part_path"] or default_part),
            row["size"] or 0,
            row["etag"],
            row["final_url"],
            row["expected_size"],
            row["attempts"] + 1,
        )

    @_locked
    def checkpoint_download(self, job: DownloadJob) -> None:
        self.conn.execute(
            "UPDATE inventory SET "
            "part_path=?,size=?,etag=?,final_url=?,expected_size=?,updated=? WHERE url=?",
            (
                str(job.part_path),
                job.durable_offset,
                job.strong_etag,
                job.final_url,
                job.expected_size,
                time.time(),
                job.url,
            ),
        )
        self.conn.commit()

    @_locked
    def mark_finalizing(self, file: DownloadedFile) -> None:
        self.conn.execute(
            "UPDATE inventory SET "
            "status='finalizing',final_path=?,sha256=?,size=?,mime_type=?,final_url=?,updated=? "
            "WHERE url=?",
            (
                str(file.path),
                file.sha256,
                file.size,
                file.mime_type,
                file.final_url,
                time.time(),
                file.url,
            ),
        )
        self.conn.commit()

    @_locked
    def mark_downloaded(self, file: DownloadedFile) -> None:
        self.conn.execute(
            "UPDATE inventory SET "
            "status='downloaded',final_path=?,sha256=?,size=?,mime_type=?,final_url=?,updated=? "
            "WHERE url=?",
            (
                str(file.path),
                file.sha256,
                file.size,
                file.mime_type,
                file.final_url,
                time.time(),
                file.url,
            ),
        )
        self.conn.commit()

    def next_retry_delay(self) -> float | None:
        row = self.conn.execute(
            "SELECT min(retry_at) FROM inventory WHERE status='retry' AND retry_at IS NOT NULL"
        ).fetchone()
        if not row or row[0] is None:
            return None
        return max(0.0, float(row[0]) - time.time())

    def pending_parse(self) -> Iterator[DownloadedFile]:
        rows = self.conn.execute(
            "SELECT i.* FROM inventory i LEFT JOIN parse_results p ON i.url=p.url WHERE "
            "i.status='downloaded' AND (p.url IS NULL OR p.status IN "
            "('cloud_failed','quota_blocked')) ORDER BY i.url"
        )
        for row in rows:
            yield DownloadedFile(
                row["url"],
                Path(row["final_path"]),
                row["sha256"],
                row["size"],
                row["mime_type"] or "application/octet-stream",
                row["final_url"] or row["url"],
            )

    @_locked
    def mark_parse(
        self,
        url: str,
        result: ParseResult,
        cache_path: Path | None = None,
        *,
        used_cloud: bool = False,
    ) -> None:
        """Store a parse result; ``cache_path`` only when a LlamaParse cache file exists."""
        self.conn.execute(
            "INSERT INTO parse_results(url,status,result_json,cache_path,used_cloud,updated) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
            "status=excluded.status,result_json=excluded.result_json,"
            "cache_path=excluded.cache_path,used_cloud=excluded.used_cloud,"
            "updated=excluded.updated",
            (
                url,
                result.status,
                json.dumps(jsonable(result), ensure_ascii=False),
                str(cache_path) if cache_path else None,
                int(used_cloud),
                time.time(),
            ),
        )
        self.conn.commit()

    @_locked
    def requeue_cloud_parses(self) -> int:
        """Forget LlamaParse results and their cache files so the next extract calls it again."""
        rows = self.conn.execute(
            "SELECT cache_path FROM parse_results WHERE used_cloud=1 AND cache_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            Path(row[0]).unlink(missing_ok=True)
        cursor = self.conn.execute("DELETE FROM parse_results WHERE used_cloud=1")
        self.conn.commit()
        return cursor.rowcount

    def parse_record(self, url: str) -> ParseResult | None:
        row = self.conn.execute(
            "SELECT result_json FROM parse_results WHERE url=?", (url,)
        ).fetchone()
        if not row or not row[0]:
            return None

        return parse_result_from_dict(json.loads(row[0]))

    @_locked
    def record_failure(
        self, url: str, stage: str, reason: str, retry_at: float | None = None
    ) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO failures VALUES(?,?,?,?,?)",
                (url, stage, reason, retry_at, now),
            )
            if stage == "download":
                self.conn.execute(
                    "UPDATE inventory SET status=?,error=?,retry_at=?,updated=? WHERE url=?",
                    ("retry" if retry_at else "failed", reason, retry_at, now, url),
                )

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

    def reset_failed_downloads(self) -> int:
        """Send failed downloads back to pending and drop their partial files."""
        with self._write_lock, self.conn:
            rows = self.conn.execute(
                "SELECT url, part_path FROM inventory WHERE status='failed'"
            ).fetchall()
            for row in rows:
                if row["part_path"]:
                    Path(row["part_path"]).unlink(missing_ok=True)
            self.conn.execute(
                "UPDATE inventory SET status='pending',attempts=0,part_path=NULL,size=NULL,"
                "etag=NULL,retry_at=NULL,error=NULL,updated=? WHERE status='failed'",
                (time.time(),),
            )
            self.conn.executemany(
                "DELETE FROM failures WHERE url=? AND stage='download'",
                [(row["url"],) for row in rows],
            )
        return len(rows)

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

    def refs_for(self, target: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM refs WHERE target_url=? ORDER BY source_url", (target,)
        )
        return [dict(r) for r in rows]

    def inventory_rows(self):
        return self.conn.execute("SELECT * FROM inventory ORDER BY url")

    def save_cloud_job(
        self,
        request_id: str,
        url: str,
        *,
        file_id: str | None = None,
        job_id: str | None = None,
        key_label: str | None = None,
        key_fingerprint: str | None = None,
        status: str = "accepted",
        payload: str | None = None,
    ) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO "
                "cloud_jobs(request_id,url,file_id,job_id,key_label,key_fingerprint,status,payload,"
                "updated) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET "
                "file_id=COALESCE(excluded.file_id,cloud_jobs.file_id),"
                "job_id=COALESCE(excluded.job_id,cloud_jobs.job_id),status=excluded.status,"
                "payload=excluded.payload,updated=excluded.updated",
                (
                    request_id,
                    url,
                    file_id,
                    job_id,
                    key_label,
                    key_fingerprint,
                    status,
                    payload,
                    time.time(),
                ),
            )
            self.conn.commit()


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
