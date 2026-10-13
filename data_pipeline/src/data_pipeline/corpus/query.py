from __future__ import annotations

from pymongo import MongoClient


def query_links(settings, snapshot: str, url: str, direction: str):
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    collection = client[settings.mongo_database].links
    field = "target_url" if direction == "incoming" else "source_url"
    rows = list(collection.find({"snapshot_id": snapshot, field: url}, {"_id": 0}))
    client.close()
    return rows
