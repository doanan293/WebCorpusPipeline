"""In-memory stand-ins for the corpus ports."""

from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from data_pipeline.domain.shared.errors import ExternalFailure


class FakeCorpusDatabase:
    """Collections as dicts of documents keyed by ``_id``; counts writes per collection."""

    def __init__(self) -> None:
        self.collections: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.writes: Counter[str] = Counter()
        self.indexed = False

    def dataset(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.collections["datasets"].get(snapshot_id)

    def put_dataset(self, doc: dict[str, Any]) -> None:
        self.collections["datasets"][doc["_id"]] = doc

    def set_dataset_fields(self, snapshot_id: str, fields: dict[str, Any]) -> None:
        doc = self.dataset(snapshot_id)
        if doc is not None:
            doc.update(fields)

    def ensure_indexes(self) -> None:
        self.indexed = True

    def replace_many(self, collection: str, docs: Iterable[dict[str, Any]]) -> int:
        written = 0
        for doc in docs:
            self.collections[collection][doc["_id"]] = dict(doc)
            written += 1
        self.writes[collection] += written
        return written

    def delete_other_snapshots(self, collection: str, keep: str) -> int:
        field = "_id" if collection == "datasets" else "snapshot_id"
        docs = self.collections[collection]
        gone = [key for key, doc in docs.items() if doc.get(field) != keep]
        for key in gone:
            del docs[key]
        return len(gone)

    def find_links(
        self, snapshot_id: str, field: str, url: str, limit: int
    ) -> list[dict[str, Any]]:
        found = sorted(
            (
                doc
                for doc in self.collections["links"].values()
                if doc.get("snapshot_id") == snapshot_id and doc.get(field) == url
            ),
            key=lambda doc: str(doc["_id"]),
        )
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in found[:limit]]


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
