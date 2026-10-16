from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.database import Database

from data_pipeline.domain.corpus.records import SNAPSHOT_COLLECTIONS
from data_pipeline.domain.shared.config import Settings

MAX_BATCH_OPERATIONS = 100
MAX_BATCH_BYTES = 4 * 1024 * 1024


def connect(settings: Settings) -> MongoClient[dict[str, Any]]:
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)


class MongoCorpusDatabase:
    """The ``CorpusDatabase`` port over one MongoDB database."""

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self.db = db

    def dataset(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.db.datasets.find_one({"_id": snapshot_id})

    def put_dataset(self, doc: dict[str, Any]) -> None:
        self.db.datasets.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def set_dataset_fields(self, snapshot_id: str, fields: dict[str, Any]) -> None:
        self.db.datasets.update_one({"_id": snapshot_id}, {"$set": fields})

    def ensure_indexes(self) -> None:
        for collection in SNAPSHOT_COLLECTIONS:
            self.db[collection].create_index([("snapshot_id", ASCENDING)])
        self.db.links.create_index([("snapshot_id", ASCENDING), ("source_url", ASCENDING)])
        self.db.links.create_index([("snapshot_id", ASCENDING), ("target_url", ASCENDING)])

    def replace_many(self, collection: str, docs: Iterable[dict[str, Any]]) -> int:
        """Upsert ``docs`` by ``_id`` in bulk writes of at most 100 operations and ~4 MiB."""
        target = self.db[collection]
        operations: list[ReplaceOne[dict[str, Any]]] = []
        batch_bytes = 0
        written = 0
        for value in docs:
            operation_value = dict(value)
            approx = len(json.dumps(operation_value, ensure_ascii=False).encode()) + 1024
            if operations and (
                len(operations) >= MAX_BATCH_OPERATIONS or batch_bytes + approx > MAX_BATCH_BYTES
            ):
                target.bulk_write(operations, ordered=False)
                operations = []
                batch_bytes = 0
            operations.append(
                ReplaceOne({"_id": operation_value["_id"]}, operation_value, upsert=True)
            )
            batch_bytes += approx
            written += 1
        if operations:
            target.bulk_write(operations, ordered=False)
        return written

    def delete_other_snapshots(self, collection: str, keep: str) -> int:
        field = "_id" if collection == "datasets" else "snapshot_id"
        return self.db[collection].delete_many({field: {"$ne": keep}}).deleted_count

    def find_links(
        self, snapshot_id: str, field: str, url: str, limit: int
    ) -> list[dict[str, Any]]:
        return list(
            self.db.links.find({"snapshot_id": snapshot_id, field: url}, {"_id": 0}, limit=limit)
        )
