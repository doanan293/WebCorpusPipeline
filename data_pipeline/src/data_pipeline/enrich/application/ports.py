from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol

from data_pipeline.enrich.domain.models import (
    DownloadedFile,
    DownloadJob,
    HttpResponse,
    LocalResult,
    ParseResult,
    RunSummary,
)
from data_pipeline.shared.domain.config import Settings


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


class WorkState(Protocol):
    """The enrichment work state, as the download use case needs it."""

    @property
    def root(self) -> Path: ...

    def claim_download(self) -> DownloadJob | None: ...

    def checkpoint_download(self, job: DownloadJob) -> None: ...

    def mark_finalizing(self, file: DownloadedFile) -> None: ...

    def mark_downloaded(self, file: DownloadedFile) -> None: ...

    def record_failure(
        self, url: str, stage: str, reason: str, retry_at: float | None = None
    ) -> None: ...

    def next_retry_delay(self) -> float | None: ...

    def summary(self) -> RunSummary: ...


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
