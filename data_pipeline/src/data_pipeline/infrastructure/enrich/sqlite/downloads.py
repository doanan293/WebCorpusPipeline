from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from data_pipeline.domain.enrich.models import DownloadedFile, DownloadJob
from data_pipeline.infrastructure.enrich.sqlite.repository import LockedRepository, locked


class SqliteDownloads(LockedRepository):
    """The ``Downloads`` port: the inventory rows as a download queue."""

    def __init__(
        self, conn: sqlite3.Connection, write_lock: AbstractContextManager[Any], root: Path
    ) -> None:
        super().__init__(conn, write_lock)
        self.root = root

    @locked
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

    @locked
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

    @locked
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

    @locked
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
