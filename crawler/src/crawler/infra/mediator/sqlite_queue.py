from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from crawler.application.mediator.ports import QueueCounts
from crawler.domain.mediator.models import QueuedUrl, UrlKind
from crawler.infra.shared.sqlite import connect, transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    url TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    next_due_at REAL,
    last_sent_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    etag TEXT,
    last_modified TEXT,
    interval_days REAL
);
CREATE INDEX IF NOT EXISTS urls_next_due ON urls (next_due_at);
CREATE TABLE IF NOT EXISTS hosts (
    host TEXT PRIMARY KEY,
    last_sent_at REAL NOT NULL
);
"""


_ADDED_COLUMNS = (("etag", "TEXT"), ("last_modified", "TEXT"), ("interval_days", "REAL"))
_COLUMNS = (
    "url, host, kind, next_due_at, last_sent_at, attempts, etag, last_modified, interval_days"
)


class SqliteUrlQueue:
    """The ``UrlQueue`` port over one SQLite file; every write commits at once."""

    def __init__(self, path: Path) -> None:
        self.db = connect(path)
        self.db.executescript(_SCHEMA)
        # Queue files from before change-aware recrawls lack these columns.
        present = {row[1] for row in self.db.execute("PRAGMA table_info(urls)")}
        with transaction(self.db):
            for column, kind in _ADDED_COLUMNS:
                if column not in present:
                    self.db.execute(f"ALTER TABLE urls ADD COLUMN {column} {kind}")

    def close(self) -> None:
        self.db.close()

    def add(self, items: Sequence[tuple[str, str, UrlKind]], now: float) -> int:
        before = self.db.total_changes
        with transaction(self.db):
            self.db.executemany(
                "INSERT OR IGNORE INTO urls (url, host, kind, next_due_at) VALUES (?, ?, ?, ?)",
                [(url, host, kind, now) for url, host, kind in items],
            )
        return self.db.total_changes - before

    def make_due(self, urls: Sequence[str], now: float) -> int:
        before = self.db.total_changes
        with transaction(self.db):
            self.db.executemany(
                "UPDATE urls SET next_due_at = ?, attempts = 0, etag = NULL, last_modified = NULL "
                "WHERE url = ?",
                [(now, url) for url in urls],
            )
        return self.db.total_changes - before

    def next_due(self, now: float, exclude_hosts: Collection[str]) -> QueuedUrl | None:
        excluded = sorted(exclude_hosts)
        marks = ", ".join("?" * len(excluded))
        row = self.db.execute(
            f"""
            SELECT {_COLUMNS}
            FROM urls
            WHERE next_due_at <= ? {f"AND host NOT IN ({marks})" if excluded else ""}
            ORDER BY next_due_at
            LIMIT 1
            """,  # noqa: S608 -- only placeholders are formatted in
            (now, *excluded),
        ).fetchone()
        return QueuedUrl(*row) if row else None

    def has_due(self, now: float) -> bool:
        row = self.db.execute("SELECT 1 FROM urls WHERE next_due_at <= ? LIMIT 1", (now,))
        return row.fetchone() is not None

    def mark_sent(self, url: str, now: float, next_due_at: float | None) -> None:
        with transaction(self.db):
            self.db.execute(
                "UPDATE urls SET last_sent_at = ?, next_due_at = ?, attempts = 0 WHERE url = ?",
                (now, next_due_at, url),
            )
            self.db.execute(
                "INSERT INTO hosts (host, last_sent_at) SELECT host, ? FROM urls WHERE url = ? "
                "ON CONFLICT (host) DO UPDATE SET last_sent_at = excluded.last_sent_at",
                (now, url),
            )

    def mark_failed(self, url: str, attempts: int, next_due_at: float | None) -> None:
        self.db.execute(
            "UPDATE urls SET attempts = ?, next_due_at = ? WHERE url = ?",
            (attempts, next_due_at, url),
        )

    def get(self, url: str) -> QueuedUrl | None:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM urls WHERE url = ?",  # noqa: S608 -- constant column list
            (url,),
        ).fetchone()
        return QueuedUrl(*row) if row else None

    def reschedule(
        self,
        url: str,
        *,
        next_due_at: float,
        interval_days: float,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        self.db.execute(
            "UPDATE urls SET next_due_at = ?, interval_days = ?, etag = ?, last_modified = ? "
            "WHERE url = ?",
            (next_due_at, interval_days, etag, last_modified, url),
        )

    def counts(self, now: float) -> QueueCounts:
        total, due = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(next_due_at <= ?), 0) FROM urls", (now,)
        ).fetchone()
        return QueueCounts(total=total, due=due)
