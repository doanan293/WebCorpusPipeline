from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol

from data_pipeline.domain.enrich.models import (
    DownloadedFile,
    DownloadJob,
    HttpResponse,
    InventorySummary,
    LocalResult,
    ParseResult,
    RunSummary,
    SourceProgress,
    SourceRef,
)
from data_pipeline.domain.shared.config import Settings


class HttpTransport(Protocol):
    async def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse: ...


type TransportFactory = Callable[[Settings], AbstractAsyncContextManager[HttpTransport]]


class DownloadFiles(Protocol):
    """Where partial and finished downloads live."""

    def resume_point(self, job: DownloadJob) -> tuple[int, dict[str, str]]: ...

    def truncate(self, job: DownloadJob) -> None: ...

    async def write_body(
        self,
        job: DownloadJob,
        body: AsyncIterator[bytes],
        offset: int,
        *,
        max_bytes: int,
        checkpoint_bytes: int,
        checkpoint: Callable[[DownloadJob], None],
    ) -> int: ...

    def size(self, job: DownloadJob) -> int: ...

    def head(self, job: DownloadJob, size: int = 2048) -> bytes: ...

    def finalize(
        self,
        job: DownloadJob,
        mime: str,
        files_root: Path,
        *,
        before_move: Callable[[DownloadedFile], None],
    ) -> DownloadedFile: ...


class Inventory(Protocol):
    """Which crawl sources were read and which files they point at."""

    def source_progress(self, source_key: str) -> SourceProgress | None: ...

    def add_inventory_batch(
        self, candidates: list[str], refs: list[SourceRef], progress: SourceProgress
    ) -> None: ...

    def inventory_done(self, source_key: str) -> bool: ...

    def inventory_counts(self) -> InventorySummary: ...

    def delete_refs_from(self, source_urls: Iterable[str]) -> None: ...

    def refs_for(self, target: str) -> list[dict[str, Any]]: ...

    def inventory_rows(self) -> Iterable[Mapping[str, Any]]: ...


class Downloads(Protocol):
    """The download queue: claim a file, checkpoint it, finish it, retry it later."""

    def claim_download(self) -> DownloadJob | None: ...

    def checkpoint_download(self, job: DownloadJob) -> None: ...

    def mark_finalizing(self, file: DownloadedFile) -> None: ...

    def mark_downloaded(self, file: DownloadedFile) -> None: ...

    def next_retry_delay(self) -> float | None: ...

    def reset_failed_downloads(self) -> int: ...


class Parses(Protocol):
    """Parse results per downloaded file, and the cloud parse jobs behind them."""

    def pending_parse(self) -> Iterator[DownloadedFile]: ...

    def mark_parse(
        self,
        url: str,
        result: ParseResult,
        cache_path: Path | None = None,
        *,
        used_cloud: bool = False,
    ) -> None: ...

    def parse_record(self, url: str) -> ParseResult | None: ...

    def requeue_cloud_parses(self) -> int: ...

    def save_cloud_job(
        self,
        request_id: str,
        url: str,
        *,
        file_id: str | None = None,
        job_id: str | None = None,
        key_label: str | None = None,
        key_fingerprint: str | None = None,
        status: str = "accepted",
        payload: str | None = None,
    ) -> None: ...


class Failures(Protocol):
    """Failed downloads and parses, with when to retry them."""

    def record_failure(
        self, url: str, stage: str, reason: str, retry_at: float | None = None
    ) -> None: ...


class EnrichUnitOfWork(Protocol):
    """One open work state: its repositories share one connection and one write lock."""

    @property
    def inventory(self) -> Inventory: ...

    @property
    def downloads(self) -> Downloads: ...

    @property
    def parses(self) -> Parses: ...

    @property
    def failures(self) -> Failures: ...

    @property
    def root(self) -> Path: ...

    def recover(self) -> None: ...

    def summary(self) -> RunSummary: ...

    def close(self) -> None: ...


class WorkState(Protocol):
    """The enrichment work state: inventory, downloads, parse results and failures."""

    @property
    def root(self) -> Path: ...

    def source_progress(self, source_key: str) -> SourceProgress | None: ...

    def add_inventory_batch(
        self, candidates: list[str], refs: list[SourceRef], progress: SourceProgress
    ) -> None: ...

    def inventory_done(self, source_key: str) -> bool: ...

    def inventory_counts(self) -> InventorySummary: ...

    def delete_refs_from(self, source_urls: Iterable[str]) -> None: ...

    def claim_download(self) -> DownloadJob | None: ...

    def checkpoint_download(self, job: DownloadJob) -> None: ...

    def mark_finalizing(self, file: DownloadedFile) -> None: ...

    def mark_downloaded(self, file: DownloadedFile) -> None: ...

    def next_retry_delay(self) -> float | None: ...

    def pending_parse(self) -> Iterator[DownloadedFile]: ...

    def mark_parse(
        self,
        url: str,
        result: ParseResult,
        cache_path: Path | None = None,
        *,
        used_cloud: bool = False,
    ) -> None: ...

    def requeue_cloud_parses(self) -> int: ...

    def parse_record(self, url: str) -> ParseResult | None: ...

    def record_failure(
        self, url: str, stage: str, reason: str, retry_at: float | None = None
    ) -> None: ...

    def recover(self) -> None: ...

    def reset_failed_downloads(self) -> int: ...

    def summary(self) -> RunSummary: ...

    def refs_for(self, target: str) -> list[dict[str, Any]]: ...

    def inventory_rows(self) -> Iterable[Mapping[str, Any]]: ...

    def save_cloud_job(
        self,
        request_id: str,
        url: str,
        *,
        file_id: str | None = None,
        job_id: str | None = None,
        key_label: str | None = None,
        key_fingerprint: str | None = None,
        status: str = "accepted",
        payload: str | None = None,
    ) -> None: ...

    def close(self) -> None: ...


class ArticleSource(Protocol):
    """The crawl's article rows, read from a byte offset so inventory can resume."""

    @property
    def key(self) -> str: ...

    def crawl_id(self) -> str | None: ...

    def stat(self) -> tuple[int, int]: ...

    def modified_pages(self) -> set[str]: ...

    def rows(self, offset: int) -> Iterator[tuple[dict[str, Any], int]]: ...


class LocalParser(Protocol):
    def parse(self, file: DownloadedFile) -> LocalResult: ...


class CloudParser(Protocol):
    def parse(
        self, document_url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult: ...


class ParseCache(Protocol):
    def read(self, key: str) -> ParseResult | None: ...

    def write(self, key: str, result: ParseResult) -> Path: ...

    def path_for(self, key: str) -> Path: ...
