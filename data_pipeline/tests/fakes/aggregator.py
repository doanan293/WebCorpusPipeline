"""In-memory stand-ins for the aggregator ports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from data_pipeline.application.aggregator.ports import OutboxEntry, Target


class MemoryAggregatorState:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.owners: dict[str, str] = {}

    def add_new(self, urls: Sequence[str]) -> list[str]:
        new = [url for url in dict.fromkeys(urls) if url not in self.seen]
        self.seen.update(new)
        return new

    def claim(self, content_id: str, url: str) -> str:
        return self.owners.setdefault(content_id, url)


class MemoryOutbox:
    def __init__(self) -> None:
        self.entries: list[OutboxEntry] = []

    def append(self, target: Target, payload: dict[str, Any]) -> None:
        next_id = max((entry.id for entry in self.entries), default=0) + 1
        self.entries.append(OutboxEntry(next_id, target, payload))

    def pending(self) -> list[OutboxEntry]:
        return list(self.entries)

    def remove(self, ids: Sequence[int]) -> None:
        self.entries = [entry for entry in self.entries if entry.id not in set(ids)]


class FakeServiceClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[Target, dict[str, Any]]] = []

    async def send(self, target: Target, payload: dict[str, Any]) -> bool:
        if self.ok:
            self.sent.append((target, payload))
        return self.ok
