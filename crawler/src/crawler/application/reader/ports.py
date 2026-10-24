from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from crawler.domain.reader.models import DownloadedFile, LocalResult, ParseResult, StoredPage


class CloudJobLog(Protocol):
    """Where a LlamaParse job is recorded while it runs, so a paid job can be found again."""

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


class LocalReader(Protocol):
    def parse(self, file: DownloadedFile) -> LocalResult: ...


class CloudReader(Protocol):
    def parse(
        self, document_url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult: ...


class ParseCache(Protocol):
    def read(self, key: str) -> ParseResult | None: ...

    def write(self, key: str, result: ParseResult) -> Path: ...

    def path_for(self, key: str) -> Path: ...


class MergerClient(Protocol):
    async def send(self, record: dict[str, Any]) -> bool:
        """Hand one record to the merger block's ``/add``."""
        ...


class RawPages(Protocol):
    def iter(self) -> Iterator[StoredPage]:
        """Every stored HTML page, one at a time."""
        ...
