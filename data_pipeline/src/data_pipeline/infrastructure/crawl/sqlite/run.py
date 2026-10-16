from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from data_pipeline.infrastructure.crawl.sqlite import export
from data_pipeline.infrastructure.crawl.sqlite.export import asset_metrics, skipped_reasons


class SqliteCrawlRun:
    """The ``CrawlRun`` port: the run's manifest, counts, finish record and published outputs."""

    def __init__(
        self, root: Path, manifest: dict[str, Any], connection: sqlite3.Connection
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.manifest_path = root / "manifest.json"
        self.connection = connection
        self.refresh_run_id: str | None = None

    def write_manifest(self, value: dict[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    @property
    def crawl_id(self) -> str:
        seed = f"{self.manifest['input_sha256']}:{self.manifest['started_at']!r}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self.connection.backup(target)
        finally:
            target.close()

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in self.connection.execute(
            "SELECT status, count(*) FROM url_records GROUP BY status;"
        ):
            result[f"url_status_{row[0]}"] = int(row[1])

        cursor = self.connection.execute("SELECT count(*) FROM url_records;")
        result["total_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM url_records WHERE completed = 1;")
        result["completed_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM articles;")
        result["articles"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM files;")
        result["files"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM errors;")
        result["errors"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM targets;")
        result["targets"] = int(cursor.fetchone()[0])

        return result

    def finish(  # noqa: PLR0913 -- one keyword per manifest field recorded at the end of a run
        self,
        status: str,
        exit_code: int,
        counts: dict[str, int],
        metrics: Mapping[str, object],
        truncation: Mapping[str, object] | None = None,
        *,
        discovery: Mapping[str, object] | None = None,
        frontier: Mapping[str, object] | None = None,
        host_completion: Mapping[str, Mapping[str, object]] | None = None,
        public_output: Path | None = None,
    ) -> None:
        finish_data: dict[str, object] = {
            "status": status,
            "exit_code": exit_code,
            "counts": counts,
            "metrics": dict(metrics),
            "truncation": dict(truncation) if truncation is not None else None,
            "truncation_reasons": sorted(truncation) if truncation is not None else [],
            "finished_at": time.time(),
        }
        if discovery is not None or self.manifest.get("phase") == "discover":
            finish_data["discovery"] = dict(discovery) if discovery is not None else None
        if frontier is not None:
            finish_data["frontier"] = dict(frontier)
        elif "frontier" in metrics and isinstance(metrics["frontier"], dict):
            finish_data["frontier"] = metrics["frontier"]
        if self.manifest.get("phase") != "discover":
            finish_data["assets"] = asset_metrics(self.connection)
            finish_data["skipped"] = skipped_reasons(self.connection)
            finish_data["host_completion"] = {
                k: dict(v) for k, v in (host_completion or {}).items()
            }

        self.manifest.update(finish_data)
        self.write_manifest(self.manifest)
        if public_output is not None:
            public_output.mkdir(parents=True, exist_ok=True)
            pub_manifest = public_output / "manifest.json"
            tmp = pub_manifest.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            tmp.replace(pub_manifest)
            if self.manifest.get("phase") == "crawl":
                export.export_final(self, public_output)

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]:
        return export.reason_counts(self.connection, reasons)

    def export_crawl(self, output: Path) -> None:
        export.export_crawl(self.connection, output)

    def export_discovery(self, output: Path) -> None:
        export.export_discovery(self.connection, output)

    def export_final(self, output: Path) -> None:
        export.export_final(self, output)
