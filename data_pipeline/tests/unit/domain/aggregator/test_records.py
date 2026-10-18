from __future__ import annotations

from data_pipeline.domain.aggregator.records import duplicate_stub


def test_duplicate_stub() -> None:
    record = {
        "url": "https://h/b",
        "format": "html",
        "title": "B",
        "content": "same text",
        "out-links": [("https://h/c", "C")],
        "intro": "x",
        "content_id": "abc",
    }
    assert duplicate_stub(record, "https://h/a") == {
        "url": "https://h/b",
        "format": "html",
        "title": "B",
        "content": "",
        "out-links": [("https://h/c", "C")],
        "content_id": "abc",
        "duplicate_of": "https://h/a",
    }
