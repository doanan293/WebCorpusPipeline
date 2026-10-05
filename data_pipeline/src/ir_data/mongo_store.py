from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pymongo import ASCENDING, MongoClient, ReplaceOne

from .package import verify_corpus


COLLECTIONS = ("datasets", "pages", "documents", "images", "links")
MAX_BATCH_BYTES = 4 * 1024 * 1024


def _record_id(value: dict, kind: str) -> str:
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


def _page_value(value: dict) -> dict:
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
        "warnings",
    )
    return {key: value[key] for key in fields if key in value}


def _replace_batches(collection, values) -> None:
    operations = []
    batch_bytes = 0
    for value in values:
        operation_value = dict(value)
        approx = len(json.dumps(operation_value, ensure_ascii=False).encode()) + 1024
        if operations and (
            len(operations) >= 100 or batch_bytes + approx > MAX_BATCH_BYTES
        ):
            collection.bulk_write(operations, ordered=False)
            operations = []
            batch_bytes = 0
        operations.append(ReplaceOne({"_id": operation_value["_id"]}, operation_value, upsert=True))
        batch_bytes += approx
    if operations:
        collection.bulk_write(operations, ordered=False)


def import_corpus(path: Path, settings) -> dict:
    report = verify_corpus(path)
    if not report["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(report["errors"]))
    snapshot = json.loads((path / "manifest.json").read_text())
    sid = snapshot["snapshot_id"]
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[settings.mongo_database]
    dataset = db.datasets.find_one({"_id": sid})
    if dataset and dataset.get("status") == "ready":
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
            if not file.exists():
                continue

            def values():
                with file.open(encoding="utf-8") as stream:
                    for line in stream:
                        value = json.loads(line)
                        if collection == "pages":
                            value = _page_value(value)
                        value["snapshot_id"] = sid
                        value["_id"] = _record_id(value, collection) + ":" + sid
                        yield value

            _replace_batches(db[collection], values())
        db.datasets.update_one({"_id": sid}, {"$set": {"status": "ready", "error": None}})
        return {"snapshot_id": sid, "status": "ready"}
    except Exception as exc:
        db.datasets.update_one({"_id": sid}, {"$set": {"status": "failed", "error": str(exc)[:1000]}})
        raise
    finally:
        client.close()
