"""In-memory stand-ins for the mediator ports."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace

from crawler.application.mediator.ports import LoadResult, QueueCounts
from crawler.domain.mediator.models import QueuedUrl, UrlKind
from crawler.domain.shared.protocol import BUSY
from crawler.domain.shared.validators import Validators


class FakeClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


class FakeUrlQueue:
    def __init__(self) -> None:
        self.rows: dict[str, QueuedUrl] = {}
        self.host_sent: dict[str, float] = {}

    def add(self, items: Sequence[tuple[str, str, UrlKind]], now: float) -> int:
        added = 0
        for url, host, kind in items:
            if url not in self.rows:
                self.rows[url] = QueuedUrl(url, host, kind, now, None, 0)
                added += 1
        return added

    def make_due(self, urls: Sequence[str]) -> int:
        known = [url for url in urls if url in self.rows]
        for url in known:
            self.rows[url] = replace(
                self.rows[url], next_due_at=0.0, attempts=0, etag=None, last_modified=None
            )
        return len(known)

    def cancel(self, urls: Sequence[str]) -> int:
        known = [url for url in urls if url in self.rows]
        for url in known:
            self.rows[url] = replace(self.rows[url], next_due_at=None)
        return len(known)

    def next_due(self, now: float, exclude_hosts: Collection[str]) -> QueuedUrl | None:
        due = [
            row
            for row in self.rows.values()
            if row.next_due_at is not None
            and row.next_due_at <= now
            and row.host not in exclude_hosts
        ]
        if not due:
            return None
        return min(due, key=lambda row: row.next_due_at or 0.0)

    def mark_sent(self, url: str, now: float, next_due_at: float | None) -> None:
        row = self.rows[url]
        self.rows[url] = replace(row, next_due_at=next_due_at, last_sent_at=now, attempts=0)
        self.host_sent[row.host] = now

    def mark_failed(self, url: str, attempts: int, next_due_at: float | None) -> None:
        self.rows[url] = replace(self.rows[url], next_due_at=next_due_at, attempts=attempts)

    def get(self, url: str) -> QueuedUrl | None:
        return self.rows.get(url)

    def reschedule(
        self,
        url: str,
        *,
        next_due_at: float | None,
        interval_days: float,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        self.rows[url] = replace(
            self.rows[url],
            next_due_at=next_due_at,
            interval_days=interval_days,
            etag=etag,
            last_modified=last_modified,
        )

    def has_due(self, now: float) -> bool:
        return self.counts(now).due > 0

    def close(self) -> None:
        return None

    def counts(self, now: float) -> QueueCounts:
        due = sum(
            1
            for row in self.rows.values()
            if row.next_due_at is not None and row.next_due_at <= now
        )
        return QueueCounts(total=len(self.rows), due=due)


class FakeDownloader:
    """``statuses`` answers ``ping`` (``None`` = unreachable); ``load_results`` answers ``load``.

    With ``busy_after_load`` a node that accepted a URL answers "đang bận" until the test resets it.
    """

    def __init__(
        self,
        statuses: dict[str, str | None],
        load_results: dict[str, LoadResult] | None = None,
    ) -> None:
        self.statuses = statuses
        self.load_results = load_results or {}
        self.busy_after_load = False
        self.loads: list[tuple[str, str]] = []
        self.validators: list[Validators | None] = []
        self.pings: list[str] = []

    async def ping(self, node: str) -> str | None:
        self.pings.append(node)
        return self.statuses.get(node)

    async def load(self, node: str, url: str, validators: Validators | None = None) -> LoadResult:
        result = self.load_results.get(node, "accepted")
        self.validators.append(validators)
        if result == "accepted":
            self.loads.append((node, url))
            if self.busy_after_load:
                self.statuses[node] = BUSY
        return result

    async def close(self) -> None:
        return None
