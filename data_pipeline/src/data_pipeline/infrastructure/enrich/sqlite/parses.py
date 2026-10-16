from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

from data_pipeline.domain.enrich.models import DownloadedFile, ParseResult, jsonable
from data_pipeline.domain.enrich.serializers import parse_result_from_dict
from data_pipeline.infrastructure.enrich.sqlite.repository import LockedRepository, locked


class SqliteParses(LockedRepository):
    """The ``Parses`` port: parse results per downloaded file and the cloud jobs behind them."""

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

    @locked
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

    @locked
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
