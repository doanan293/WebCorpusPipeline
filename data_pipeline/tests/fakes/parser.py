"""In-memory stand-ins for the parser ports."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from data_pipeline.domain.parser.models import DownloadedFile, LocalResult, ParseResult


class FakeAggregatorClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.records: list[dict[str, Any]] = []

    async def send(self, record: dict[str, Any]) -> bool:
        self.records.append(record)
        return self.ok


class FakeCloud:
    def __init__(
        self, parse: Callable[[DownloadedFile, LocalResult, list[int]], ParseResult]
    ) -> None:
        self._parse = parse
        self.calls: list[str] = []

    def parse(
        self, document_url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        self.calls.append(document_url)
        return self._parse(file, local, missing)


class MemoryParseCache:
    def __init__(self) -> None:
        self.results: dict[str, ParseResult] = {}

    def read(self, key: str) -> ParseResult | None:
        return self.results.get(key)

    def write(self, key: str, result: ParseResult) -> Path:
        self.results[key] = result
        return Path(f"{key}.json")

    def path_for(self, key: str) -> Path:
        return Path(f"{key}.json")
