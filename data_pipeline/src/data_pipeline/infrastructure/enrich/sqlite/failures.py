from __future__ import annotations

import time

from data_pipeline.infrastructure.enrich.sqlite.repository import LockedRepository, locked


class SqliteFailures(LockedRepository):
    """The ``Failures`` port: failed downloads and parses, and when to retry them."""

    @locked
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
