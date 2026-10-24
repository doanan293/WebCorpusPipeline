from __future__ import annotations

from crawler.domain.storage.documents import to_write


def test_html_write_keeps_slide_and_extra_fields() -> None:
    write = to_write(
        {
            "url": "https://h/a",
            "format": "html",
            "title": "A",
            "content": "c",
            "out-links": [("https://h/b", "B")],
            "published_at": "2026-01-01",
            "foo": 1,
        },
        "2026-10-18T00:00:00Z",
    )
    assert write.collection == "html"
    assert write.document == {
        "url": "https://h/a",
        "format": "html",
        "title": "A",
        "content": "c",
        "intro": "",
        "published_at": "2026-01-01",
        "updated_at": "2026-10-18T00:00:00Z",
        "checked_at": "2026-10-18T00:00:00Z",
        "changed_at": "2026-10-18T00:00:00Z",
    }
    assert write.edges == [{"src": "https://h/a", "dest": "https://h/b", "intro": "B"}]


def test_other_write() -> None:
    write = to_write(
        {"url": "https://h/a.pdf", "format": "pdf", "title": "", "content": "t", "out-links": []},
        "now",
    )
    assert write.collection == "other"
    assert write.document == {
        "url": "https://h/a.pdf",
        "format": "pdf",
        "content": "t",
        "title": "",
        "updated_at": "now",
        "checked_at": "now",
        "changed_at": "now",
    }
    assert write.edges == []
