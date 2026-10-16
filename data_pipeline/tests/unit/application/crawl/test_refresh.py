from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from data_pipeline.application.crawl.refresh import (
    complete_refresh_run,
    new_run_id,
    preflight,
    prepare_refresh_run,
    select_recent,
    select_targets,
)
from data_pipeline.application.crawl.refresh_pipeline import RefreshSession
from data_pipeline.domain.crawl.refresh_rules import (
    RefreshSettings,
    classify_refetch,
    conditional_headers,
    response_validators,
)
from data_pipeline.infrastructure.crawl.sqlite.refresh_store import RefreshStore
from data_pipeline.infrastructure.crawl.sqlite.storage import SqliteCrawlStorage
from data_pipeline.infrastructure.crawl.sqlite.unit_of_work import SqliteCrawlUnitOfWork

STORAGE = SqliteCrawlStorage()

NOW = 1_790_000_000.0  # 2026-09-21
RECENT = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(NOW - 3 * 86400))
OLD = "2020-01-01T08:00:00+07:00"


def open_state(tmp_path: Path, *, resume: bool = False) -> SqliteCrawlUnitOfWork:
    input_path = tmp_path / "seeds.txt"
    if not input_path.exists():
        input_path.write_text("a.test\n", encoding="utf-8")
    return SqliteCrawlUnitOfWork.open(
        tmp_path / "out" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"crawl_strategy": "hybrid-unified"},
        runtime_config={},
        resume=resume,
    )


def put_page(
    state: SqliteCrawlUnitOfWork,
    url: str,
    *,
    role: str = "content",
    article: dict[str, object] | None = None,
    record: dict[str, object] | None = None,
) -> None:
    state.urls.complete_url(
        {
            "url": url,
            "status": "extracted",
            "frontier_action": "scheduled",
            "response_purpose": "page",
            "discovery_source": "html_link",
            "seed_type": "recursive",
            "content_id": hashlib.sha256(url.encode()).hexdigest(),
            **(record or {}),
        },
        article={"url": url, "final_url": url, "text": url, "page_role": role, **(article or {})},
    )


def test_preflight_rejects_missing_and_unfinished_crawls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing crawl"):
        preflight(tmp_path / "out" / "state", STORAGE)
    state = open_state(tmp_path)
    state.close()
    with pytest.raises(ValueError, match="finished crawl"):
        preflight(tmp_path / "out" / "state", STORAGE)


def test_preflight_starts_new_or_continues_interrupted_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.run.finish("complete", exit_code=0, counts={}, metrics={})
    state.close()
    assert preflight(tmp_path / "out" / "state", STORAGE) is None

    state = open_state(tmp_path, resume=True)
    store = state.refresh
    store.start_run("20261006T000000Z")
    store.finish_run("20261006T000000Z", "interrupted", {})
    state.run.finish("interrupted", exit_code=130, counts={}, metrics={})
    state.close()
    assert preflight(tmp_path / "out" / "state", STORAGE) == "20261006T000000Z"


def test_select_targets_collects_each_hub_kind(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/")
    put_page(state, "https://a.test/tin-tuc")
    put_page(
        state,
        "https://a.test/tin-tuc?page=2",
        role="navigation",
        record={
            "family_key": "a.test/tin-tuc?page={page}",
            "ordinal": 2,
            "discovered_from": "https://a.test/tin-tuc",
        },
    )
    put_page(state, "https://a.test/bai/recent", article={"published_at": RECENT})
    put_page(state, "https://a.test/bai/old", article={"published_at": OLD})
    put_page(state, "https://a.test/bai/undated")
    put_page(state, "https://a.test/bai/from-run", record={"first_seen_run": "20260920T000000Z"})
    for url, purpose in (
        ("https://a.test/robots.txt", "robots"),
        ("https://a.test/sitemap.xml", "sitemap"),
    ):
        state.urls.upsert_url(
            {"url": url, "status": "scheduled", "response_purpose": purpose}, completed=True
        )
    state.urls.upsert_url(
        {
            "url": "https://b.test/robots.txt",
            "status": "scheduled",
            "response_purpose": "robots",
        },
        completed=True,
    )

    targets = select_targets(
        state.urls,
        state.articles,
        frozenset({"a.test"}),
        RefreshSettings(),
        ["20260920T000000Z"],
        NOW,
    )

    assert targets == [
        ("https://a.test/", "homepage"),
        ("https://a.test/bai/from-run", "recent"),
        ("https://a.test/bai/recent", "recent"),
        ("https://a.test/robots.txt", "sitemap"),
        ("https://a.test/sitemap.xml", "sitemap"),
        ("https://a.test/tin-tuc", "pagination_head"),
        ("https://a.test/tin-tuc?page=2", "navigation"),
    ]
    state.close()


def test_select_recent_orders_newest_first_and_ignores_bad_dates(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    day = 86400
    put_page(
        state,
        "https://a.test/a",
        article={"published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 5 * day))},
    )
    put_page(
        state,
        "https://a.test/b",
        article={"published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 1 * day))},
    )
    put_page(
        state,
        "https://a.test/c",
        article={"modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 2 * day))},
    )
    put_page(state, "https://a.test/bad", article={"published_at": "08/09/2026", "modified_at": ""})

    recent = select_recent(state.articles, RefreshSettings(recheck_limit=2), [], NOW)

    assert recent == ["https://a.test/b", "https://a.test/c"]
    state.close()


def test_validators_round_trip_to_conditional_headers() -> None:
    headers = {b"ETag": b'"v1"', b"Last-Modified": b"Tue, 06 Oct 2026 00:00:00 GMT"}
    validators = response_validators(headers.get)
    assert validators == {"etag": '"v1"', "last_modified": "Tue, 06 Oct 2026 00:00:00 GMT"}
    assert conditional_headers(validators) == {
        "If-None-Match": '"v1"',
        "If-Modified-Since": "Tue, 06 Oct 2026 00:00:00 GMT",
    }
    assert conditional_headers({}) == {}


def test_classify_refetch() -> None:
    assert classify_refetch(None, "x", None) == "new"
    assert classify_refetch("x", "x", None) == "unchanged"
    assert classify_refetch("x", "y", None) == "modified"
    assert classify_refetch("x", "y", "soft_404") == "gate_soft_404"


def test_follow_pagination_stops_after_configured_empty_pages(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    session = RefreshSession(
        RefreshStore(state.connection), "R", RefreshSettings(pagination_empty_pages=2)
    )
    assert session.follow_pagination("f", 3) is True
    assert session.follow_pagination("f", 0) is True
    assert session.follow_pagination("f", 0) is False
    assert session.follow_pagination("g", 0) is True
    state.close()


def test_prepare_new_run_backs_up_and_stores_targets(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/")
    store = state.refresh
    output = tmp_path / "out"

    run_id, targets = prepare_refresh_run(
        state, output, frozenset({"a.test"}), RefreshSettings(), None, now=NOW
    )

    assert run_id == new_run_id(NOW)
    assert targets == [("https://a.test/", "homepage")]
    assert state.run.refresh_run_id == run_id
    assert (output / "archive" / f"pre-refresh-{run_id}" / "index.sqlite3").exists()
    assert (store.latest_run() or {})["status"] == "running"
    state.close()


def test_prepare_keeps_run_ids_strictly_increasing(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = state.refresh
    store.start_run(new_run_id(NOW))
    store.finish_run(new_run_id(NOW), "complete", {})

    run_id, _ = prepare_refresh_run(
        state, tmp_path / "out", frozenset({"a.test"}), RefreshSettings(), None, now=NOW
    )

    assert run_id == new_run_id(NOW + 1)
    state.close()


def test_prepare_continues_interrupted_run_without_new_backup(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = state.refresh
    store.start_run("20261006T000000Z")
    store.add_targets(
        "20261006T000000Z", [("https://a.test/", "homepage"), ("https://a.test/x", "recent")]
    )
    store.finish_target("20261006T000000Z", "https://a.test/", "unchanged")
    store.finish_run("20261006T000000Z", "interrupted", {})

    run_id, targets = prepare_refresh_run(
        state,
        tmp_path / "out",
        frozenset({"a.test"}),
        RefreshSettings(),
        "20261006T000000Z",
        now=NOW,
    )

    assert run_id == "20261006T000000Z"
    assert targets == [("https://a.test/x", "recent")]
    assert not (tmp_path / "out" / "archive").exists()
    assert (store.latest_run() or {})["status"] == "running"
    state.close()


def test_complete_refresh_run_exports_changes(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/new")
    store = state.refresh
    output = tmp_path / "out"
    run_id, _ = prepare_refresh_run(state, output, frozenset(), RefreshSettings(), None, now=NOW)
    session = RefreshSession(store, run_id, RefreshSettings())
    session.record_change("https://a.test/new", "new", page_role="content", content_id="c1")

    summary = complete_refresh_run(state.run, state.articles, session, output, "complete")

    run_dir = output / "changes" / run_id
    changes = [
        json.loads(line)
        for line in (run_dir / "changes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    articles = [
        json.loads(line)
        for line in (run_dir / "articles.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [c["url"] for c in changes] == ["https://a.test/new"]
    assert [a["url"] for a in articles] == ["https://a.test/new"]
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert summary["new"] == 1 and summary["modified"] == 0
    assert summary["crawl_id"] == state.run.crawl_id
    assert state.run.manifest["last_refresh_run_id"] == run_id
    assert (store.latest_run() or {})["status"] == "complete"
    state.close()


def test_interrupted_refresh_run_exports_nothing(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = state.refresh
    output = tmp_path / "out"
    run_id, _ = prepare_refresh_run(state, output, frozenset(), RefreshSettings(), None, now=NOW)

    complete_refresh_run(
        state.run,
        state.articles,
        RefreshSession(store, run_id, RefreshSettings()),
        output,
        "interrupted",
    )

    assert not (output / "changes").exists()
    assert "last_refresh_run_id" not in state.run.manifest
    assert (store.latest_run() or {})["status"] == "interrupted"
    state.close()


def test_failed_prepare_restores_finished_crawl_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = open_state(tmp_path)
    state.run.finish("complete", exit_code=0, counts={}, metrics={})
    state.close()
    state = open_state(tmp_path, resume=True)
    assert state.run.manifest["status"] == "running"

    def disk_full(destination: Path) -> None:
        msg = "No space left on device"
        raise OSError(msg)

    monkeypatch.setattr(state.run, "backup_to", disk_full)
    with pytest.raises(OSError):
        prepare_refresh_run(
            state,
            tmp_path / "out",
            frozenset({"a.test"}),
            RefreshSettings(),
            None,
            now=NOW,
        )
    state.close()

    assert preflight(tmp_path / "out" / "state", STORAGE) is None


def test_homepage_target_skips_https_when_only_http_homepage_works(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.urls.upsert_url({"url": "https://a.test/", "status": "failed"}, completed=True)
    put_page(state, "http://a.test/")

    targets = select_targets(
        state.urls, state.articles, frozenset({"a.test"}), RefreshSettings(), [], NOW
    )

    assert ("http://a.test/", "homepage") in targets
    assert ("https://a.test/", "homepage") not in targets
    state.close()


def test_refresh_recent_uses_parsed_nukeviet_date(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    day = time.strftime("%Y-%m-%d", time.gmtime(NOW - 2 * 86400))
    put_page(state, "https://a.test/nv", article={"published_at": f"{day}+0710:00:00"})

    assert select_recent(state.articles, RefreshSettings(), [], NOW) == ["https://a.test/nv"]
    state.close()
