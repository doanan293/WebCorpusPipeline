from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceRef:
    source_url: str
    target_url: str
    relation: str = "hyperlink"
    anchor_text: str = ""
    alt: str = ""
    caption: str = ""


@dataclass(frozen=True)
class SourceProgress:
    """How far inventory read one source file; a different size or mtime means a new file."""

    path: str
    size: int
    mtime_ns: int
    cursor: int
    done: bool = False


@dataclass
class DownloadJob:
    url: str
    part_path: Path
    durable_offset: int = 0
    strong_etag: str | None = None
    final_url: str | None = None
    expected_size: int | None = None
    attempts: int = 0


@dataclass
class HttpResponse:
    """One HTTP response as the downloader sees it; ``close`` releases the connection."""

    status: int
    headers: Mapping[str, str]
    final_url: str
    body: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]


@dataclass
class RunSummary:
    downloaded: int = 0
    parsed: int = 0
    failed: int = 0
    pending: int = 0
    quota_blocked: int = 0
    cloud_failed: int = 0
    transferred_bytes: int = 0


@dataclass
class InventorySummary:
    candidates: int
    referrers: int
