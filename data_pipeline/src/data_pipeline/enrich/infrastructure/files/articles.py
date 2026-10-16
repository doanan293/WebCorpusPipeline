from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from data_pipeline.enrich.domain.inventory_rules import clean_url


def source_key(source: Path) -> str:
    articles = source / "articles.jsonl" if source.is_dir() else source
    return str(articles.resolve())


class JsonlArticleSource:
    """A crawl's ``articles.jsonl`` (or ``changes.jsonl``) and the files written beside it."""

    def __init__(self, source: Path) -> None:
        self.articles = source / "articles.jsonl" if source.is_dir() else source
        self.key = source_key(source)

    def crawl_id(self) -> str | None:
        folder = self.articles.parent
        for name in ("README.json", "summary.json"):
            path = folder / name
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get("crawl_id")
            except (json.JSONDecodeError, AttributeError):
                continue
            if value:
                return str(value)
        return None

    def stat(self) -> tuple[int, int]:
        if not self.articles.exists():
            raise FileNotFoundError(self.articles)
        stat = self.articles.stat()
        return stat.st_size, stat.st_mtime_ns

    def modified_pages(self) -> set[str]:
        changes_path = self.articles.parent / "changes.jsonl"
        if not changes_path.exists():
            return set()
        pages: set[str] = set()
        for line in changes_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            change = json.loads(line)
            if change.get("change") == "modified" and change.get("url"):
                pages.add(clean_url(str(change["url"])))
        return pages

    def rows(self, offset: int) -> Iterator[tuple[dict[str, Any], int]]:
        """Yield each row from byte ``offset`` on, with the offset just after it."""
        with self.articles.open("rb") as stream:
            stream.seek(offset)
            while line := stream.readline():
                yield json.loads(line), stream.tell()
