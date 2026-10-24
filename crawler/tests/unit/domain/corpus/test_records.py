from __future__ import annotations

from crawler.domain.corpus.records import (
    CORPUS_COLLECTIONS,
    collection_file,
    normalize_query_url,
)


def test_collections_follow_the_slide() -> None:
    assert CORPUS_COLLECTIONS == ("html", "other", "graph")
    assert collection_file("graph") == "graph.jsonl"


def test_query_url_drops_fragment() -> None:
    assert normalize_query_url(" https://h/a#x ") == "https://h/a"
