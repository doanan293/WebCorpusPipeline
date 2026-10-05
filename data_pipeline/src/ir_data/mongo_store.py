from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pymongo import ASCENDING, MongoClient, UpdateOne

from .package import verify_corpus


COLLECTIONS = ("datasets", "pages", "documents", "images", "links")


def _record_id(value: dict, kind: str) -> str:
    raw = json.dumps(
        [
            kind,
            value.get("url") or value.get("source_url"),
            value.get("target_url"),
            value.get("anchor_text"),
            value.get("alt"),
            value.get("caption"),
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


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
        operations = []
        with file.open(encoding="utf-8") as stream:
            for line in stream:
                value = json.loads(line)
                value["snapshot_id"] = sid
                value["_id"] = _record_id(value, collection) + ":" + sid
                operations.append(UpdateOne({"_id": value["_id"]}, {"$set": value}, upsert=True))
                if len(operations) >= 500:
                    db[collection].bulk_write(operations, ordered=False)
                    operations = []
        if operations:
            db[collection].bulk_write(operations, ordered=False)
    db.datasets.update_one({"_id": sid}, {"$set": {"status": "ready"}})
    client.close()
    return {"snapshot_id": sid, "status": "ready"}
