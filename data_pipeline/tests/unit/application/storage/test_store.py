from __future__ import annotations

from data_pipeline.application.storage.store import store_record
from tests.fakes.storage import FakeCorpusStore


def _record(links: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "url": "https://h/a",
        "format": "html",
        "title": "A",
        "content": "c",
        "out-links": links,
    }


def test_store_replaces_edges_of_source() -> None:
    store = FakeCorpusStore()
    store_record(_record([("https://h/b", "B"), ("https://h/c", "C")]), store, "t1")
    store_record(_record([("https://h/d", "D")]), store, "t2")
    assert store.graph == [{"src": "https://h/a", "dest": "https://h/d", "text": "D"}]
    assert store.collections["html"]["https://h/a"]["updated_at"] == "t2"
