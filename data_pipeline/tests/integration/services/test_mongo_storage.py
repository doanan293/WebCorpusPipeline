from __future__ import annotations

import os
import uuid

import pytest
from pymongo import MongoClient

from data_pipeline.application.storage.store import store_record
from data_pipeline.infrastructure.storage.mongo import MongoCorpusStore

pytestmark = pytest.mark.docker


def test_mongo_upsert_and_edges() -> None:
    client: MongoClient[dict[str, object]] = MongoClient(
        os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000
    )
    name = f"test_{uuid.uuid4().hex}"
    try:
        store = MongoCorpusStore(client[name])
        store.ensure_indexes()
        record = {
            "url": "https://h/a",
            "format": "html",
            "title": "A",
            "content": "c",
            "out-links": [("https://h/b", "B")],
        }
        store_record(record, store, "t1")
        store_record({**record, "title": "A2", "out-links": [("https://h/c", "C")]}, store, "t2")
        db = client[name]
        assert db.html.count_documents({}) == 1
        assert db.html.find_one({"url": "https://h/a"}, {"_id": 0})["title"] == "A2"  # type: ignore[index]
        assert [edge["dest"] for edge in db.graph.find({"src": "https://h/a"})] == ["https://h/c"]
    finally:
        client.drop_database(name)
