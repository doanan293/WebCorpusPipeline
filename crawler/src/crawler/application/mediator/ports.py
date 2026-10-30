from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from crawler.domain.mediator.models import QueuedUrl, UrlKind
from crawler.domain.shared.validators import Validators

type LoadResult = Literal["accepted", "busy", "failed"]


@dataclass(frozen=True, slots=True)
class QueueCounts:
    total: int
    due: int


class UrlQueue(Protocol):
    def add(self, items: Sequence[tuple[str, str, UrlKind]], now: float) -> int:
        """Insert ``(url, host, kind)`` rows not yet known, due now; return how many were new."""
        ...

    def make_due(self, urls: Sequence[str], now: float) -> int:
        """Make known URLs due now, without validators; return how many changed."""
        ...

    def next_due(self, now: float, exclude_hosts: Collection[str]) -> QueuedUrl | None:
        """The earliest due URL whose host is not in ``exclude_hosts``."""
        ...

    def mark_sent(self, url: str, now: float, next_due_at: float | None) -> None: ...

    def mark_failed(self, url: str, attempts: int, next_due_at: float | None) -> None: ...

    def get(self, url: str) -> QueuedUrl | None: ...

    def reschedule(
        self,
        url: str,
        *,
        next_due_at: float,
        interval_days: float,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        """Set the URL's next visit and period, and the validators of its last download."""
        ...

    def counts(self, now: float) -> QueueCounts: ...

    def has_due(self, now: float) -> bool:
        """Whether any URL is due (cheaper than ``counts`` on a large queue)."""
        ...

    def close(self) -> None: ...


class Downloader(Protocol):
    async def ping(self, node: str) -> str | None:
        """The node's ``/ping`` status, or ``None`` when it cannot be reached."""
        ...

    async def load(self, node: str, url: str, validators: Validators | None = None) -> LoadResult:
        """Send one URL to the node's ``/load``: 2xx accepted, 409 busy, anything else failed."""
        ...

    async def close(self) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...
