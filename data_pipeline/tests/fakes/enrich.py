"""In-memory stand-ins for the enrich ports."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from data_pipeline.enrich.domain.models import (
    DownloadedFile,
    HttpResponse,
    LocalResult,
    ParseResult,
)
from data_pipeline.shared.domain.config import Settings


async def noop_close() -> None:
    return None


class FakeTransport:
    """Hands out queued replies and records the headers each request carried."""

    def __init__(self, *replies: HttpResponse) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, str] | None] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.requests.append(headers)
        return self.replies.pop(0)


class Served:
    """A transport factory serving one reply per URL; counts sessions and requests."""

    def __init__(self, replies: dict[str, Callable[[], HttpResponse]]) -> None:
        self.replies = replies
        self.sessions = 0
        self.calls: list[str] = []

    def __call__(self, _settings: Settings) -> Served:
        return self

    async def __aenter__(self) -> Served:
        self.sessions += 1
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.calls.append(url)
        return self.replies[url]()


class FakeArticleSource:
    """Article rows held in memory; each row's offset is its index plus one."""

    def __init__(self, rows: list[dict[str, Any]], crawl_id: str | None = "c1") -> None:
        self.key = "memory://articles"
        self._rows = rows
        self._crawl_id = crawl_id

    def crawl_id(self) -> str | None:
        return self._crawl_id

    def stat(self) -> tuple[int, int]:
        return len(self._rows), 0

    def modified_pages(self) -> set[str]:
        return set()

    def rows(self, offset: int) -> Iterator[tuple[dict[str, Any], int]]:
        for index in range(offset, len(self._rows)):
            yield self._rows[index], index + 1


class FakeLocalParser:
    def __init__(self, parse: Callable[[DownloadedFile], LocalResult]) -> None:
        self._parse = parse

    def parse(self, file: DownloadedFile) -> LocalResult:
        return self._parse(file)


class FakeCloudParser:
    """Calls ``parse(document_url, file, local, missing)``; records each document URL."""

    def __init__(
        self, parse: Callable[[str, DownloadedFile, LocalResult, list[int]], ParseResult]
    ) -> None:
        self._parse = parse
        self.calls: list[str] = []

    def parse(
        self, document_url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        self.calls.append(document_url)
        return self._parse(document_url, file, local, missing)
