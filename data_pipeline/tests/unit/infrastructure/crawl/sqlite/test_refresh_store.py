from __future__ import annotations

from pathlib import Path

from data_pipeline.infrastructure.crawl.sqlite.refresh_store import RefreshStore
from data_pipeline.infrastructure.crawl.sqlite.state_store import SqliteCrawlState

RUN = "20261006T000000Z"


def open_state(tmp_path: Path) -> SqliteCrawlState:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    return SqliteCrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={},
        runtime_config={},
    )


def test_run_lifecycle_and_latest_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run("20261001T000000Z")
    store.finish_run("20261001T000000Z", "complete", {"new": 1})
    store.start_run(RUN)

    assert (store.latest_run() or {})["run_id"] == RUN
    assert (store.latest_run() or {})["status"] == "running"
    assert store.completed_run_ids(4) == ["20261001T000000Z"]

    store.finish_run(RUN, "interrupted", {})
    store.resume_run(RUN)
    assert (store.latest_run() or {})["status"] == "running"
    state.close()


def test_targets_are_unique_per_run_and_tracked(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run(RUN)
    store.add_targets(RUN, [("https://a.test/", "homepage"), ("https://a.test/list", "navigation")])

    assert store.add_target(RUN, "https://a.test/list", "pagination_next") is False
    assert store.add_target(RUN, "https://a.test/list?page=2", "pagination_next") is True

    store.finish_target(RUN, "https://a.test/", "not_modified")
    store.finish_target(RUN, "https://a.test/list", "http_500", "server error")

    assert store.pending_targets(RUN) == [("https://a.test/list?page=2", "pagination_next")]
    assert store.target_summary(RUN) == {
        "targets_by_reason": {"homepage": 1, "navigation": 1, "pagination_next": 1},
        "outcomes": {"http_500": 1, "not_modified": 1, "pending": 1},
    }
    state.close()


def test_changes_are_upserted_and_ordered(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.record_change(RUN, {"url": "https://a.test/b", "change": "new"})
    store.record_change(RUN, {"url": "https://a.test/a", "change": "new"})
    store.record_change(RUN, {"url": "https://a.test/a", "change": "modified"})

    assert [(c["url"], c["change"], c["run_id"]) for c in store.iter_changes(RUN)] == [
        ("https://a.test/a", "modified", RUN),
        ("https://a.test/b", "new", RUN),
    ]
    state.close()


def test_failure_counts_only_cover_this_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run(RUN)
    store.add_targets(RUN, [("https://a.test/", "homepage"), ("https://a.test/x", "recent")])
    store.finish_target(RUN, "https://a.test/", "request_failed")
    store.finish_target(RUN, "https://a.test/x", "unchanged")
    state.upsert_url({"url": "https://a.test/old", "status": "failed"})
    state.upsert_url({"url": "https://a.test/new", "status": "failed", "first_seen_run": RUN})
    state.upsert_url(
        {
            "url": "https://a.test/cut",
            "status": "skipped",
            "reason": "host_url_limit",
            "first_seen_run": RUN,
        }
    )

    assert store.failure_counts(RUN, {"host_url_limit"}) == (2, 1)
    state.close()
