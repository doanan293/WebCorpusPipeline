from __future__ import annotations

from data_pipeline.domain.corpus.records import (
    normalize_query_url,
    page_value,
    record_id,
    snapshot_record,
)


def test_page_value_keeps_only_searchable_fields() -> None:
    assert page_value({"url": "u", "text": "t", "content_html": "<p>"}) == {"url": "u", "text": "t"}


def test_record_ids_differ_by_relation_and_query_url_drops_fragment() -> None:
    base = {"source_url": "a", "target_url": "b", "anchor_text": "x"}
    assert record_id({**base, "relation": "hyperlink"}, "links") != record_id(
        {**base, "relation": "image"}, "links"
    )
    assert normalize_query_url(" https://a.test/hub#top ") == "https://a.test/hub"


def test_page_value_keeps_the_duplicate_owner() -> None:
    page = page_value(
        {"url": "https://example.test/copy", "duplicate_of": "https://example.test/a"}
    )
    assert page["duplicate_of"] == "https://example.test/a"


def test_snapshot_record_ids_are_per_snapshot() -> None:
    row = {"url": "https://a.test/", "content_html": "<p>"}
    record = snapshot_record(dict(row), "pages", "s1")
    assert record == {"url": "https://a.test/", "snapshot_id": "s1", "_id": record["_id"]}
    assert record["_id"].endswith(":s1")
    assert snapshot_record(dict(row), "pages", "s2")["_id"] != record["_id"]
