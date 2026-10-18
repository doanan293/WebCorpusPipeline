from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import islice
from pathlib import Path

from data_pipeline.infrastructure.shared.sqlite import connect, transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_urls (url TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS content_owners (content_id TEXT PRIMARY KEY, url TEXT NOT NULL);
"""


class SqliteAggregatorState:
    """The ``SeenUrls`` and ``ContentOwners`` ports over one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.db = connect(path)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add_new(self, urls: Sequence[str]) -> list[str]:
        insert = "INSERT OR IGNORE INTO seen_urls VALUES (?)"
        with transaction(self.db):
            return [url for url in dict.fromkeys(urls) if self.db.execute(insert, (url,)).rowcount]

    def claim(self, content_id: str, url: str) -> str:
        with transaction(self.db):
            self.db.execute("INSERT OR IGNORE INTO content_owners VALUES (?, ?)", (content_id, url))
            (owner,) = self.db.execute(
                "SELECT url FROM content_owners WHERE content_id = ?", (content_id,)
            ).fetchone()
        return owner

    def import_seen(self, urls: Iterable[str]) -> int:
        """Record known URLs in bulk (one-off batch-crawl import); return how many are new."""
        return self._bulk("INSERT OR IGNORE INTO seen_urls VALUES (?)", ((url,) for url in urls))

    def import_owners(self, pairs: Iterable[tuple[str, str]]) -> int:
        return self._bulk("INSERT OR IGNORE INTO content_owners VALUES (?, ?)", pairs)

    def _bulk(self, sql: str, rows: Iterable[tuple[str, ...]]) -> int:
        before = self.db.total_changes
        iterator = iter(rows)
        while batch := list(islice(iterator, 10_000)):
            with transaction(self.db):
                self.db.executemany(sql, batch)
        return self.db.total_changes - before
