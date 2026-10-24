from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.database import Database

from crawler.domain.shared.config import Settings

BATCH = 1000


def connect(settings: Settings) -> MongoClient[dict[str, Any]]:
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)


class MongoCorpusDatabase:
    """The ``CorpusDatabase`` port over the slide's collections of one MongoDB database."""

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self.db = db

    def iter_collection(self, name: str) -> Iterator[dict[str, Any]]:
        return iter(self.db[name].find({}, {"_id": 0}).sort("_id", ASCENDING))

    def load(self, name: str, docs: Iterable[dict[str, Any]]) -> int:
        written = 0
        rows = iter(docs)
        while batch := list(islice(rows, BATCH)):
            self.db[name].bulk_write(
                [ReplaceOne(self._key(name, doc), doc, upsert=True) for doc in batch],
                ordered=False,
            )
            written += len(batch)
        return written

    @staticmethod
    def _key(name: str, doc: dict[str, Any]) -> dict[str, Any]:
        if name == "graph":
            # As storage keeps them: one edge per (src, dest, intro), so links differing only in
            # their sentence both survive a pull.
            return {"src": doc["src"], "dest": doc["dest"], "intro": doc.get("intro", "")}
        return {"url": doc["url"]}

    def find_edges(self, *, src: str | None, dest: str | None, limit: int) -> list[dict[str, Any]]:
        query = {key: value for key, value in (("src", src), ("dest", dest)) if value is not None}
        cursor = self.db.graph.find(query, {"_id": 0}).sort([("src", 1), ("dest", 1)])
        return list(cursor.limit(limit))

    def ensure_indexes(self) -> None:
        for name in ("html", "other"):
            self.db[name].create_index([("url", ASCENDING)], unique=True)
        self.db.graph.create_index([("src", ASCENDING)])
        self.db.graph.create_index([("dest", ASCENDING)])
