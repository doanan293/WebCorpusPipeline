from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from crawler.infra.shared.sqlite import connect, transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_urls (url TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS content_owners (content_id TEXT PRIMARY KEY, url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scope_hosts (host TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS fingerprints (url TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
"""


class SqliteMergerState:
    """The ``MergerState`` port over one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.db = connect(path)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def fingerprint(self, url: str) -> str | None:
        row = self.db.execute(
            "SELECT fingerprint FROM fingerprints WHERE url = ?", (url,)
        ).fetchone()
        return row[0] if row else None

    def save_fingerprint(self, url: str, fingerprint: str) -> None:
        self.db.execute(
            "INSERT INTO fingerprints VALUES (?, ?) "
            "ON CONFLICT (url) DO UPDATE SET fingerprint = excluded.fingerprint",
            (url, fingerprint),
        )

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

    def add_scope_hosts(self, hosts: Iterable[str]) -> None:
        with transaction(self.db):
            self.db.executemany(
                "INSERT OR IGNORE INTO scope_hosts VALUES (?)", [(host,) for host in hosts]
            )

    def scope_hosts(self) -> frozenset[str]:
        return frozenset(row[0] for row in self.db.execute("SELECT host FROM scope_hosts"))
