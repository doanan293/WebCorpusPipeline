from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

type Target = Literal["storage", "mediator"]
# ok: accepted; retry: try again later (down, 5xx); dead: refused for good (4xx), never resent.
type SendResult = Literal["ok", "retry", "dead"]


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: int
    target: Target
    payload: dict[str, Any]


class SeenUrls(Protocol):
    def add_new(self, urls: Sequence[str]) -> list[str]:
        """Record ``urls``; return the ones not seen before, in order."""
        ...


class ContentOwners(Protocol):
    def claim(self, content_id: str, url: str) -> str:
        """The URL owning this content; ``url`` becomes the owner when there is none."""
        ...


class Outbox(Protocol):
    def append(self, target: Target, payload: dict[str, Any]) -> None: ...

    def dead(self, target: Target, payload: dict[str, Any], reason: str) -> None:
        """Keep a request the other block refused for good, apart from the ones to resend."""
        ...

    def pending(self) -> Iterable[OutboxEntry]:
        """Waiting requests, oldest first; may be read lazily, so iterate once."""
        ...

    def remove(self, ids: Sequence[int]) -> None: ...


class ServiceClient(Protocol):
    async def send(self, target: Target, payload: dict[str, Any]) -> SendResult:
        """``POST /add`` to the storage or mediator block."""
        ...


class ScopeHosts(Protocol):
    def add_scope_hosts(self, hosts: Iterable[str]) -> None: ...

    def scope_hosts(self) -> frozenset[str]:
        """Every host a seed ever named: links are followed only to these."""
        ...


class MergerState(SeenUrls, ContentOwners, ScopeHosts, Protocol):
    """The memories of the merger, kept by one store."""

    def close(self) -> None: ...
