"""In-memory stand-ins for the enrich ports."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_pipeline.domain.enrich.models import (
    DownloadJob,
    HttpResponse,
    InventorySummary,
    RunSummary,
    SourceProgress,
    SourceRef,
)
from data_pipeline.domain.parser.models import DownloadedFile, LocalResult, ParseResult
from data_pipeline.domain.shared.config import Settings


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


@dataclass
class _EnrichMemory:
    """What the fake enrich repositories of one unit of work share."""

    inventory_is_done: bool = False
    requeued: bool = False


class FakeInventory:
    """An inventory that is empty; ``inventory_done`` answers from the shared memory."""

    def __init__(self, memory: _EnrichMemory) -> None:
        self.memory = memory

    def source_progress(self, source_key: str) -> SourceProgress | None:
        return None

    def add_inventory_batch(
        self, candidates: list[str], refs: list[SourceRef], progress: SourceProgress
    ) -> None:
        return None

    def inventory_done(self, source_key: str) -> bool:
        return self.memory.inventory_is_done

    def inventory_counts(self) -> InventorySummary:
        return InventorySummary(0, 0)

    def delete_refs_from(self, source_urls: Iterable[str]) -> None:
        return None

    def refs_for(self, target: str) -> list[dict[str, Any]]:
        return []

    def inventory_rows(self) -> Iterable[Mapping[str, Any]]:
        return []


class FakeDownloads:
    """A download queue with nothing in it."""

    def __init__(self, memory: _EnrichMemory) -> None:
        self.memory = memory

    def claim_download(self) -> DownloadJob | None:
        return None

    def checkpoint_download(self, job: DownloadJob) -> None:
        return None

    def mark_finalizing(self, file: DownloadedFile) -> None:
        return None

    def mark_downloaded(self, file: DownloadedFile) -> None:
        return None

    def next_retry_delay(self) -> float | None:
        return None

    def reset_failed_downloads(self) -> int:
        return 0


class FakeParses:
    """Parse results that are never stored; a cloud requeue is noted in the shared memory."""

    def __init__(self, memory: _EnrichMemory) -> None:
        self.memory = memory

    def pending_parse(self) -> Iterator[DownloadedFile]:
        return iter(())

    def mark_parse(
        self,
        url: str,
        result: ParseResult,
        cache_path: Path | None = None,
        *,
        used_cloud: bool = False,
    ) -> None:
        return None

    def parse_record(self, url: str) -> ParseResult | None:
        return None

    def requeue_cloud_parses(self) -> int:
        self.memory.requeued = True
        return 3

    def save_cloud_job(self, request_id: str, url: str, **fields: str | None) -> None:
        return None


class FakeFailures:
    """Failures that are ignored."""

    def __init__(self, memory: _EnrichMemory) -> None:
        self.memory = memory

    def record_failure(
        self, url: str, stage: str, reason: str, retry_at: float | None = None
    ) -> None:
        return None


class FakeEnrichUnitOfWork:
    """A work state that holds nothing, split into the repositories of a unit of work."""

    def __init__(self, root: Path = Path("work"), summary: RunSummary | None = None) -> None:
        self.root = root
        self.memory = _EnrichMemory()
        self.inventory = FakeInventory(self.memory)
        self.downloads = FakeDownloads(self.memory)
        self.parses = FakeParses(self.memory)
        self.failures = FakeFailures(self.memory)
        self._summary = summary or RunSummary(downloaded=2, parsed=1)

    def recover(self) -> None:
        return None

    def summary(self) -> RunSummary:
        return self._summary

    def close(self) -> None:
        return None
