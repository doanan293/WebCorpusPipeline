from __future__ import annotations

from data_pipeline.domain.crawl.exit_codes import TRUNCATION_REASONS, finish_reason_to_exit


def test_frontier_limits_are_truncation_reasons() -> None:
    assert {"host_url_limit", "total_url_limit"} <= TRUNCATION_REASONS


def test_finished_frontier_limit_returns_truncated() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=0, truncated=1) == ("truncated", 4)


def test_queued_nonfinished_run_is_interrupted() -> None:
    assert finish_reason_to_exit("shutdown", queued=2, failed=0, truncated=0) == (
        "interrupted",
        130,
    )


def test_shutdown_with_queue_is_interrupted_not_complete() -> None:
    assert finish_reason_to_exit("shutdown", queued=12, failed=0, truncated=0) == (
        "interrupted",
        130,
    )


def test_natural_finish_with_truncation_has_exit_four() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=0, truncated=1) == (
        "truncated",
        4,
    )


def test_natural_finish_with_failures_has_exit_three() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=2, truncated=0) == (
        "complete_with_failures",
        3,
    )


def test_clean_finish_has_exit_zero() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=0, truncated=0) == (
        "complete",
        0,
    )


def test_queued_urls_mean_interrupted_even_if_scrapy_says_finished() -> None:
    # A forced (double Ctrl-C) shutdown leaves no finish_reason; the queue is the evidence.
    assert finish_reason_to_exit("finished", queued=66543, failed=224) == ("interrupted", 130)
