from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.collection import Collection

from data_pipeline.corpus.package import verify_corpus
from data_pipeline.shared.domain.config import Settings

MAX_BATCH_OPERATIONS = 100

COLLECTIONS = ("datasets", "pages", "documents", "images", "links")
MAX_BATCH_BYTES = 4 * 1024 * 1024


def _record_id(value: dict[str, Any], kind: str) -> str:
    identity = [
        kind,
        value.get("url") or value.get("source_url"),
        value.get("target_url"),
    ]
    if kind == "links":
        identity.append(value.get("relation"))
    identity.extend(
        [
            value.get("anchor_text"),
            value.get("alt"),
            value.get("caption"),
        ]
    )
    raw = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _page_value(value: dict[str, Any]) -> dict[str, Any]:
    """Keep Mongo page records focused on searchable metadata and plain text."""
    fields = (
        "url",
        "final_url",
        "title",
        "author",
        "published_at",
        "modified_at",
        "summary",
        "text",
        "language",
        "canonical_url",
        "page_role",
        "status",
        "content_type",
        "content_id",
        "duplicate_of",
        "warnings",
    )
    return {key: value[key] for key in fields if key in value}


def _snapshot_values(file: Path, collection: str, snapshot_id: str) -> Iterator[dict[str, Any]]:
    with file.open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if collection == "pages":
                value = _page_value(value)
            value["snapshot_id"] = snapshot_id
            value["_id"] = _record_id(value, collection) + ":" + snapshot_id
            yield value


def _replace_batches(collection: Collection[Any], values: Iterable[dict[str, Any]]) -> None:
    operations: list[ReplaceOne[dict[str, Any]]] = []
    batch_bytes = 0
    for value in values:
        operation_value = dict(value)
        approx = len(json.dumps(operation_value, ensure_ascii=False).encode()) + 1024
        if operations and (
            len(operations) >= MAX_BATCH_OPERATIONS or batch_bytes + approx > MAX_BATCH_BYTES
        ):
            collection.bulk_write(operations, ordered=False)
            operations = []
            batch_bytes = 0
        operations.append(ReplaceOne({"_id": operation_value["_id"]}, operation_value, upsert=True))
        batch_bytes += approx
    if operations:
        collection.bulk_write(operations, ordered=False)


def connect(settings: Settings) -> MongoClient[dict[str, Any]]:
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)


def import_corpus(path: Path, db: Any, *, force: bool = False) -> dict[str, Any]:
    """Import a verified corpus into ``db``; a ready snapshot is skipped unless ``force``."""
    report = verify_corpus(path)
    if not report["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(report["errors"]))
    snapshot = json.loads((path / "manifest.json").read_text())
    sid = snapshot["snapshot_id"]
    dataset = db.datasets.find_one({"_id": sid})
    if dataset and dataset.get("status") == "ready" and not force:
        return {"snapshot_id": sid, "status": "already_ready"}
    db.datasets.replace_one(
        {"_id": sid},
        {"_id": sid, "snapshot_id": sid, "status": "importing", "manifest": snapshot},
        upsert=True,
    )
    try:
        for collection in COLLECTIONS[1:]:
            db[collection].create_index([("snapshot_id", ASCENDING)])
        db.links.create_index([("snapshot_id", ASCENDING), ("source_url", ASCENDING)])
        db.links.create_index([("snapshot_id", ASCENDING), ("target_url", ASCENDING)])
        for name, collection in (
            ("articles.jsonl", "pages"),
            ("documents.jsonl", "documents"),
            ("images.jsonl", "images"),
            ("links.jsonl", "links"),
        ):
            file = path / name
            if file.exists():
                _replace_batches(db[collection], _snapshot_values(file, collection, sid))
        db.datasets.update_one({"_id": sid}, {"$set": {"status": "ready", "error": None}})
    except Exception as exc:
        db.datasets.update_one(
            {"_id": sid}, {"$set": {"status": "failed", "error": str(exc)[:1000]}}
        )
        raise
    return {"snapshot_id": sid, "status": "ready"}


def prune_snapshots(db: Any, keep: str) -> dict[str, int]:
    """Delete every snapshot but ``keep`` (which must be ready); return deletions per collection."""
    dataset = db.datasets.find_one({"_id": keep})
    if not dataset or dataset.get("status") != "ready":
        msg = f"snapshot {keep} is not ready in MongoDB; refusing to prune the others"
        raise ValueError(msg)
    removed = {
        name: db[name].delete_many({"snapshot_id": {"$ne": keep}}).deleted_count
        for name in COLLECTIONS[1:]
    }
    removed["datasets"] = db.datasets.delete_many({"_id": {"$ne": keep}}).deleted_count
    return removed
