from __future__ import annotations

from typing import Any

from pymongo import MongoClient

from data_pipeline.core.settings import Settings


def query_links(
    settings: Settings, snapshot: str, url: str, direction: str
) -> list[dict[str, Any]]:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    collection = client[settings.mongo_database].links
    field = "target_url" if direction == "incoming" else "source_url"
    rows = list(collection.find({"snapshot_id": snapshot, field: url}, {"_id": 0}))
    client.close()
    return rows
