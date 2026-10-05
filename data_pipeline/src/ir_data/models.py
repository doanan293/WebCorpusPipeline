from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceRef:
    source_url: str
    target_url: str
    relation: str = "hyperlink"
    anchor_text: str = ""
    alt: str = ""
    caption: str = ""


@dataclass(frozen=True)
class Candidate:
    url: str
    mime_hint: str | None = None


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
class DownloadedFile:
    url: str
    path: Path
    sha256: str
    size: int
    mime_type: str
    final_url: str


@dataclass
class ExtractedPart:
    index: int
    kind: str
    text: str
    has_visual_content: bool = False
    parser: str = ""


@dataclass
class LocalResult:
    source_sha256: str
    parts: list[ExtractedPart]
    warnings: list[str] = field(default_factory=list)
    parser_version: str = ""


@dataclass
class ParseResult:
    source_sha256: str
    parts: list[ExtractedPart]
    status: str
    warnings: list[str] = field(default_factory=list)
    parser_versions: dict[str, str] = field(default_factory=dict)


@dataclass
class RunSummary:
    downloaded: int = 0
    parsed: int = 0
    failed: int = 0
    pending: int = 0
    quota_blocked: int = 0
    transferred_bytes: int = 0
    exit_code: int = 0


@dataclass
class InventorySummary:
    candidates: int
    referrers: int
    completed: bool


def jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    return value
