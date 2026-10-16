from typing import Any, cast

import pytest

from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.seeds import SeedSet


def test_hybrid_crawl_defaults_to_content_only() -> None:
    options = CrawlOptions()
    assert options.assets == "content-only"
    assert options.pagination_empty_pages == 3
    assert options.max_urls_per_host == 100_000
    assert options.max_total_urls == 1_000_000
    assert options.max_file_bytes == 100 * 1024 * 1024
    assert options.max_total_file_bytes == 100 * 1024**3


def test_all_is_the_only_opt_in_asset_mode() -> None:
    assert CrawlOptions(assets="all").assets == "all"
    with pytest.raises(ValueError, match="content-only or all"):
        CrawlOptions(assets=cast(Any, "documents"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pagination_empty_pages", 0),
        ("max_urls_per_host", 0),
        ("max_total_urls", 0),
        ("max_file_bytes", 0),
        ("max_total_file_bytes", 0),
    ],
)
def test_frontier_limits_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        CrawlOptions(**cast(Any, {field: value}))


def test_semantic_snapshot_includes_seed_scope() -> None:
    seeds = SeedSet(frozenset({"a.test"}), ("https://b.test/exact",), ())
    snapshot = CrawlOptions().semantic_snapshot(seeds)
    assert snapshot["recursive_hostnames"] == ["a.test"]
    assert snapshot["exact_urls"] == ["https://b.test/exact"]
