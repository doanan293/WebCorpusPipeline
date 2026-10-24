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


def _other(url: str, content: str = "chữ") -> dict[str, Any]:
    return {"url": url, "format": "docx", "title": "", "content": content, "out-links": []}


def test_pictures_and_document_origin() -> None:
    store = FakeCorpusStore()
    first = _html("https://a/p1", [("https://a/d.docx", "Tải")])
    store_record({**first, "images": ["https://a/i.jpg"]}, store, "t1")
    store_record(
        {
            **_html("https://a/p2", [("https://a/d.docx", "Tải")]),
            "images": ["https://a/i.jpg", "https://a/j.png"],
        },
        store,
        "t2",
    )
    store_record(_other("https://a/d.docx"), store, "t3")
    pic = store.collections["picture"]["https://a/i.jpg"]
    assert (pic["origin"], pic["format"], pic["b64"], pic["error"]) == (
        "https://a/p1",
        "jpg",
        "",
        None,
    )
    assert store.collections["picture"]["https://a/j.png"]["origin"] == "https://a/p2"
    assert store.collections["other"]["https://a/d.docx"]["origin"] == "https://a/p1"


def test_document_origin_survives_a_rewrite() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://a/p1", [("https://a/d.docx", "Tải")]), store, "t1")
    store_record(_other("https://a/d.docx", "v1"), store, "t2")
    store_record(_html("https://a/p1"), store, "t3")  # the link is gone now
    store_record(_other("https://a/d.docx", "v2"), store, "t4")
    assert store.collections["other"]["https://a/d.docx"]["origin"] == "https://a/p1"


def test_document_nobody_links_to_has_empty_origin() -> None:
    store = FakeCorpusStore()
    store_record(_other("https://a/seed.pdf"), store, "t1")
    assert store.collections["other"]["https://a/seed.pdf"]["origin"] == ""


def test_intros_are_written_in_one_batch_for_stored_targets_only() -> None:
    store = FakeCorpusStore()
    store_record(_html("https://a/b"), store, "t1")
    store_record(_html("https://a/c"), store, "t2")
    store.intro_batches.clear()
    links = [("https://a/b", "B"), ("https://a/c", "C"), ("https://x/elsewhere", "X")]
    store_record(_html("https://a/", links), store, "t3")
    assert store.intro_batches == [{"https://a/": "", "https://a/b": "B", "https://a/c": "C"}]
