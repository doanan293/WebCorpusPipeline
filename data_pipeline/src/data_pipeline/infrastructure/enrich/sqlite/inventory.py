from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from data_pipeline.domain.enrich.models import InventorySummary, SourceProgress, SourceRef
from data_pipeline.infrastructure.enrich.sqlite.repository import LockedRepository, locked


class SqliteInventory(LockedRepository):
    """The ``Inventory`` port: files found in the crawl, the pages that link them, read progress."""

    def source_progress(self, source_key: str) -> SourceProgress | None:
        row = self.conn.execute(
            "SELECT size, mtime_ns, cursor, done FROM inventory_sources WHERE path=?",
            (source_key,),
        ).fetchone()
        if row is None:
            return None
        return SourceProgress(source_key, int(row[0]), int(row[1]), int(row[2]), bool(row[3]))

    @locked
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

    def inventory_counts(self) -> InventorySummary:
        return InventorySummary(
            self.conn.execute("SELECT count(*) FROM inventory").fetchone()[0],
            self.conn.execute("SELECT count(*) FROM refs").fetchone()[0],
        )

    @locked
    def delete_refs_from(self, source_urls: Iterable[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "DELETE FROM refs WHERE source_url=?", [(url,) for url in source_urls]
            )

    def refs_for(self, target: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM refs WHERE target_url=? ORDER BY source_url", (target,)
        )
        return [dict(r) for r in rows]

    def inventory_rows(self) -> Iterator[dict[str, Any]]:
        return (dict(row) for row in self.conn.execute("SELECT * FROM inventory ORDER BY url"))
