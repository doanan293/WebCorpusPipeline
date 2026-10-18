from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import DocumentTooLarge, WriteError

from data_pipeline.application.storage.ports import StoreRejectedError
from data_pipeline.infrastructure.storage.mongo import MongoCorpusStore


class _Collection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def replace_one(self, *args: Any, **kwargs: Any) -> None:
        raise self.error


class _Database:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(self.error)


@pytest.mark.parametrize("error", [DocumentTooLarge("17 MB"), WriteError("bad", code=2)])
def test_refused_writes_are_store_rejected(error: Exception) -> None:
    store = MongoCorpusStore(_Database(error))  # type: ignore[arg-type]
    with pytest.raises(StoreRejectedError):
        store.upsert("other", {"url": "https://h/big.pdf", "content": "x"})


class _Graph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def bulk_write(self, requests: list[Any], ordered: bool = True) -> None:
        self.calls.append(("bulk_write", [request._filter for request in requests]))

    def delete_many(self, query: dict[str, Any]) -> None:
        self.calls.append(("delete_many", query))

    def insert_many(self, docs: list[Any], ordered: bool = True) -> None:
        self.calls.append(("insert_many", docs))


class _GraphDatabase:
    def __init__(self) -> None:
        self.graph = _Graph()


def test_replace_edges_keeps_edges_until_new_ones_written() -> None:
    db = _GraphDatabase()
    store = MongoCorpusStore(db)  # type: ignore[arg-type]
    edges = [
        {"src": "https://h/", "dest": "https://h/a", "text": "A"},
        {"src": "https://h/", "dest": "https://h/a", "text": "A"},
        {"src": "https://h/", "dest": "https://h/b", "text": "B"},
    ]
    store.replace_edges("https://h/", edges)
    (first, written), (second, query) = db.graph.calls
    assert first == "bulk_write"
    assert written == [
        {"src": "https://h/", "dest": "https://h/a", "text": "A"},
        {"src": "https://h/", "dest": "https://h/b", "text": "B"},
    ]
    assert second == "delete_many"
    assert query == {
        "src": "https://h/",
        "$nor": [{"dest": "https://h/a", "text": "A"}, {"dest": "https://h/b", "text": "B"}],
    }


def test_replace_edges_with_no_links_clears_the_page() -> None:
    db = _GraphDatabase()
    MongoCorpusStore(db).replace_edges("https://h/", [])  # type: ignore[arg-type]
    assert db.graph.calls == [("delete_many", {"src": "https://h/"})]
