from __future__ import annotations

import os
import uuid

import pytest
from pymongo import MongoClient

from data_pipeline.infrastructure.corpus.mongo import MongoCorpusDatabase

pytestmark = pytest.mark.docker


def test_load_iter_and_find_edges() -> None:
    client: MongoClient[dict[str, object]] = MongoClient(
        os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000
    )
    name = f"test_{uuid.uuid4().hex}"
    try:
        db = MongoCorpusDatabase(client[name])
        db.ensure_indexes()
        page = {"url": "https://h/a", "format": "html", "title": "A", "content": "a", "intro": ""}
        assert db.load("html", [page, {**page, "title": "A2"}]) == 2
        edges = [{"src": "https://h/a", "dest": "https://h/b", "text": "B"}] * 2
        db.load("graph", edges)
        assert [doc["title"] for doc in db.iter_collection("html")] == ["A2"]
        assert db.find_edges(src="https://h/a", dest=None, limit=10) == [
            {"src": "https://h/a", "dest": "https://h/b", "text": "B"}
        ]
    finally:
        client.drop_database(name)
