from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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
