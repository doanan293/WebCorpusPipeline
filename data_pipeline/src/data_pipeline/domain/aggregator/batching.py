from __future__ import annotations

from collections.abc import Sequence


class UrlBatcher:
    """New URLs for the controller: a batch goes when full or after ``max_wait`` seconds."""

    def __init__(self, max_size: int = 500, max_wait: float = 1.0) -> None:
        self.max_size = max_size
        self.max_wait = max_wait
        self.pending: list[str] = []
        self.started: float | None = None

    def add(self, urls: Sequence[str], now: float) -> list[list[str]]:
        """Queue ``urls``; return the batches that are now full."""
        if urls and self.started is None:
            self.started = now
        self.pending.extend(urls)
        full: list[list[str]] = []
        while len(self.pending) >= self.max_size:
            full.append(self.pending[: self.max_size])
            self.pending = self.pending[self.max_size :]
        if not self.pending:
            self.started = None
        return full

    def due(self, now: float) -> list[str] | None:
        """The waiting batch once it is ``max_wait`` old, else ``None``."""
        if self.started is None or now - self.started < self.max_wait:
            return None
        batch = self.pending
        self.pending = []
        self.started = None
        return batch
