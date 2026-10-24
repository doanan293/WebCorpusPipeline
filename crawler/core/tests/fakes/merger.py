"""In-memory stand-ins for the merger ports."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from data_pipeline.application.merger.ports import OutboxEntry, SendResult, Target


class MemoryMergerState:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.owners: dict[str, str] = {}
        self.hosts: set[str] = set()

    def add_new(self, urls: Sequence[str]) -> list[str]:
        new = [url for url in dict.fromkeys(urls) if url not in self.seen]
        self.seen.update(new)
        return new

    def claim(self, content_id: str, url: str) -> str:
        return self.owners.setdefault(content_id, url)

    def add_scope_hosts(self, hosts: Iterable[str]) -> None:
        self.hosts.update(hosts)

    def scope_hosts(self) -> frozenset[str]:
        return frozenset(self.hosts)

    def close(self) -> None:
        return None


class MemoryOutbox:
    def __init__(self) -> None:
        self.entries: list[OutboxEntry] = []
        self.dead_letters: list[tuple[Target, dict[str, Any], str]] = []

    def dead(self, target: Target, payload: dict[str, Any], reason: str) -> None:
        self.dead_letters.append((target, payload, reason))

    def append(self, target: Target, payload: dict[str, Any]) -> None:
        next_id = max((entry.id for entry in self.entries), default=0) + 1
        self.entries.append(OutboxEntry(next_id, target, payload))

    def pending(self) -> list[OutboxEntry]:
        return list(self.entries)

    def remove(self, ids: Sequence[int]) -> None:
        self.entries = [entry for entry in self.entries if entry.id not in set(ids)]


class FakeServiceClient:
    """Answers every send with ``answer(target, payload)`` (``"ok"`` by default), or raises ``error``."""

    def __init__(
        self,
        ok: bool = True,
        *,
        answer: Callable[[Target, dict[str, Any]], SendResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        default: SendResult = "ok" if ok else "retry"

        def constant(target: Target, payload: dict[str, Any]) -> SendResult:
            return default

        self.answer = answer or constant
        self.error = error
        self.sent: list[tuple[Target, dict[str, Any]]] = []
        self.attempts: list[tuple[Target, dict[str, Any]]] = []

    async def send(self, target: Target, payload: dict[str, Any]) -> SendResult:
        self.attempts.append((target, payload))
        if self.error is not None:
            raise self.error
        result = self.answer(target, payload)
        if result == "ok":
            self.sent.append((target, payload))
        return result
