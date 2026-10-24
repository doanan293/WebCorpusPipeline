from __future__ import annotations

from crawler.application.corpus.query import query_links
from tests.fakes.corpus import FakeCorpusDatabase


def _db() -> FakeCorpusDatabase:
    db = FakeCorpusDatabase()
    db.load(
        "graph",
        [
            {"src": "https://h/a", "dest": "https://h/b", "intro": "B"},
            {"src": "https://h/c", "dest": "https://h/b", "intro": "B2"},
            {"src": "https://h/b", "dest": "https://h/a", "intro": "A"},
        ],
    )
    return db


def test_incoming_links_drop_the_fragment_of_the_query() -> None:
    found = query_links(_db(), "https://h/b#top", direction="incoming", limit=10)
    assert [edge["src"] for edge in found] == ["https://h/a", "https://h/c"]


def test_outgoing_links_respect_the_limit() -> None:
    db = _db()
    db.load("graph", [{"src": "https://h/a", "dest": "https://h/z", "intro": "Z"}])
    assert len(query_links(db, "https://h/a", direction="outgoing", limit=1)) == 1
