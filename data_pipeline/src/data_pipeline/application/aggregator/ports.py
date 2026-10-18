from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

type Target = Literal["storage", "controller"]


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

    def pending(self) -> list[OutboxEntry]: ...

    def remove(self, ids: Sequence[int]) -> None: ...


class ServiceClient(Protocol):
    async def send(self, target: Target, payload: dict[str, Any]) -> bool:
        """``POST /add`` to the storage or controller block; ``True`` when it was accepted."""
        ...
