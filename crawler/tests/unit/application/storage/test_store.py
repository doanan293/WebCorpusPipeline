from __future__ import annotations

from typing import Any

from crawler.application.storage.store import store_record
from tests.fakes.storage import FakeCorpusStore


def _html(
    url: str, links: list[tuple[str, str]] | None = None, content: str = "c"
) -> dict[str, Any]:
    return {
        "url": url,
        "format": "html",
        "title": "A",
        "content": content,
        "out-links": links or [],
    }


def test_store_replaces_edges_of_source() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://h/a", [("https://h/b", "B"), ("https://h/c", "C")]), store, "t1")
    store_record(_html("https://h/a", [("https://h/d", "D")]), store, "t2")
    assert store.graph == [{"src": "https://h/a", "dest": "https://h/d", "intro": "D"}]
    assert store.collections["html"]["https://h/a"]["updated_at"] == "t2"


def test_storing_a_page_fills_intro_of_its_targets() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://a/b"), store, "t1")
    store_record(_html("https://a/", [("https://a/b", "Tin B"), ("https://a/x", "X")]), store, "t2")
    store_record(
        _html("https://a/c", [("https://a/b", "Tin B"), ("https://a/b", "Xem")]), store, "t3"
    )
    assert store.collections["html"]["https://a/b"]["intro"] == "Tin B\nXem"
    assert "https://a/x" not in store.collections["html"]  # no record for a target never stored


def test_a_page_stored_later_gets_intro_from_links_already_seen() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://a/", [("https://a/b", "Tin B")]), store, "t1")
    store_record(_html("https://a/b"), store, "t2")
    assert store.collections["html"]["https://a/b"]["intro"] == "Tin B"


def test_unchanged_only_touches_checked_at() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://a/b", content="v1"), store, "t1")
    store_record({"url": "https://a/b", "change": "unchanged"}, store, "t2")
    store_record({"url": "https://a/b", "change": "not_modified"}, store, "t3")
    doc = store.collections["html"]["https://a/b"]
    assert (doc["content"], doc["checked_at"], doc["changed_at"]) == ("v1", "t3", "t1")
