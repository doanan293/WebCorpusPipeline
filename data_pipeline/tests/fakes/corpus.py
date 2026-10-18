"""In-memory stand-ins for the corpus ports."""

from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from data_pipeline.domain.shared.errors import ExternalFailure


class FakeCorpusDatabase:
    """The slide's collections in memory: ``html``/``other`` keyed by url, ``graph`` by (src, dest)."""

    def __init__(self) -> None:
        self.collections: defaultdict[str, dict[Any, dict[str, Any]]] = defaultdict(dict)
        self.writes: Counter[str] = Counter()
        self.indexed = False

    @staticmethod
    def _key(name: str, doc: dict[str, Any]) -> Any:
        return (doc["src"], doc["dest"]) if name == "graph" else doc["url"]

    def iter_collection(self, name: str) -> Iterator[dict[str, Any]]:
        return iter([dict(doc) for doc in self.collections[name].values()])

    def load(self, name: str, docs: Iterable[dict[str, Any]]) -> int:
        written = 0
        for doc in docs:
            self.collections[name][self._key(name, doc)] = dict(doc)
            written += 1
        self.writes[name] += written
        return written

    def find_edges(self, *, src: str | None, dest: str | None, limit: int) -> list[dict[str, Any]]:
        found = [
            edge
            for edge in self.collections["graph"].values()
            if (src is None or edge["src"] == src) and (dest is None or edge["dest"] == dest)
        ]
        return sorted(found, key=lambda edge: (edge["src"], edge["dest"]))[:limit]

    def ensure_indexes(self) -> None:
        self.indexed = True


class FakePublisher:
    """Records what it is asked to upload; serves downloads from a local folder."""

    def __init__(self, remote: Path | None = None, *, bulk_fails: bool = False) -> None:
        self.remote = remote
        self.bulk_fails = bulk_fails
        self.uploads: list[tuple[str, list[str]]] = []
        self.downloads: list[str] = []

    def create(self, folder: Path) -> None:
        self.uploads.append(("create", sorted(p.name for p in folder.iterdir())))

    def version(self, folder: Path, message: str) -> None:
        self.uploads.append(("version", sorted(p.name for p in folder.iterdir())))

    def download(self, dataset: str, folder: Path) -> None:
        self.downloads.append(dataset)
        if self.bulk_fails:
            msg = "404 archive not found"
            raise ExternalFailure(msg)
        for name in self.list_files(dataset):
            self.download_file(dataset, name, folder)

    def list_files(self, dataset: str) -> list[str]:
        assert self.remote is not None
        return sorted(p.name for p in self.remote.iterdir())

    def download_file(self, dataset: str, name: str, folder: Path) -> None:
        assert self.remote is not None
        shutil.copy2(self.remote / name, folder / name)
