import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pytest

from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter
from data_pipeline.crawl.state.store import (
    CrawlState,
)


def fail_disk_write(*args: Any) -> None:
    msg = "disk"
    raise OSError(msg)


def open_state(
    root: Path,
    phase: Literal["discover", "crawl"] = "crawl",
    *,
    resume: bool = False,
    semantic_config: Mapping[str, object] | None = None,
    runtime_config: Mapping[str, object] | None = None,
) -> CrawlState:
    state_dir = (
        root
        if root.name == "state" or (root / "manifest.json").exists()
        else root / "output" / "state"
    )
    input_path = state_dir.parent / "input.txt"
    if not input_path.exists():
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("a.test\n", encoding="utf-8")
    return CrawlState.open(
        state_dir,
        phase=phase,
        input_path=input_path,
        semantic_config={} if semantic_config is None else semantic_config,
        runtime_config={} if runtime_config is None else runtime_config,
        resume=resume,
    )


def test_resume_rejects_a_different_phase(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="discover")
    state.close()
    with pytest.raises(ValueError, match="phase"):
        open_state(tmp_path, phase="crawl", resume=True)


def test_late_discovery_cannot_downgrade_completed_success(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url(
        {"url": "https://a.test/article", "status": "extracted", "http_status": 200},
        article={"url": "https://a.test/article", "title": "Keep me"},
    )

    state.upsert_url(
        {
            "url": "https://a.test/article",
            "status": "skipped",
            "reason": "host_out_of_scope",
        }
    )

    record = next(state.iter_url_records())
    assert record["status"] == "extracted"
    assert record["http_status"] == 200
    assert state.is_complete("https://a.test/article")
    state.close()


def test_finish_publishes_only_current_extracted_articles_to_final(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    output = tmp_path / "output"
    state.complete_url(
        {"url": "https://a.test/keep", "status": "extracted"},
        article={
            "url": "https://a.test/keep",
            "title": "Keep",
            "duplicate_of": "https://a.test/stale",
        },
    )
    state.put_article({"url": "https://a.test/stale", "title": "Stale", "text": "Full text"})
    state.upsert_url({"url": "https://a.test/stale", "status": "skipped", "reason": "action_auth"})

    state.finish("complete_with_failures", 3, state.counts(), {}, public_output=output)

    final = output / "final"
    articles = [json.loads(line) for line in (final / "articles.jsonl").read_text().splitlines()]
    assert articles == [{"url": "https://a.test/keep", "title": "Keep", "text": "Full text"}]
    assert (final / "urls.txt").read_text() == "https://a.test/keep\n"
    summary = json.loads((final / "README.json").read_text())
    assert summary["article_count"] == summary["url_count"] == 1
    assert sorted(path.name for path in final.iterdir()) == [
        "README.json",
        "articles.jsonl",
        "urls.txt",
    ]
    state.close()


def test_finish_refreshes_final_after_resume(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    output = tmp_path / "output"
    state.complete_url(
        {"url": "https://a.test/first", "status": "extracted"},
        article={"url": "https://a.test/first"},
    )
    state.finish("complete", 0, state.counts(), {}, public_output=output)
    assert (output / "final/urls.txt").read_text() == "https://a.test/first\n"

    state.complete_url(
        {"url": "https://a.test/second", "status": "extracted"},
        article={"url": "https://a.test/second"},
    )
    state.finish("complete", 0, state.counts(), {}, public_output=output)

    assert (output / "final/urls.txt").read_text() == (
        "https://a.test/first\nhttps://a.test/second\n"
    )
    assert [
        json.loads(line)["url"]
        for line in (output / "final/articles.jsonl").read_text().splitlines()
    ] == ["https://a.test/first", "https://a.test/second"]
    assert json.loads((output / "final/README.json").read_text())["article_count"] == 2
    state.close()


def test_resume_rejects_semantic_change_but_accepts_runtime_change(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.close()
    source = tmp_path / "output" / "input.txt"
    with pytest.raises(ValueError, match="semantic configuration"):
        CrawlState.open(
            tmp_path / "output/state",
            phase="crawl",
            input_path=source,
            semantic_config={"assets": "all"},
            runtime_config={"max_concurrency": 64},
            resume=True,
        )

    resumed = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config={},
        runtime_config={"max_concurrency": 32},
        resume=True,
    )
    assert resumed.manifest["runtime_history"][-1] == {"max_concurrency": 32}
    resumed.close()


def test_claim_content_deduplication(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    owner1 = state.claim_content("hash-123", "https://a.test/first")
    owner2 = state.claim_content("hash-123", "https://a.test/second")
    assert owner1 == "https://a.test/first"
    assert owner2 == "https://a.test/first"
    state.close()


def test_interruption_safe_reopen(tmp_path: Path) -> None:
    state1 = open_state(tmp_path)
    state1.upsert_url({"url": "https://a.test/done", "status": "extracted"})
    state1.checkpoint("https://a.test/done")
    state1.upsert_url({"url": "https://a.test/pending", "status": "discovered_not_downloaded"})
    state1.close()

    source = tmp_path / "output" / "input.txt"
    state2 = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config={},
        runtime_config={"max_concurrency": 64},
        resume=True,
    )
    assert state2.is_complete("https://a.test/done")
    assert not state2.is_complete("https://a.test/pending")
    state2.close()


def test_counts_and_finish_manifest(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/1", "status": "extracted"}, completed=True)
    state.upsert_url({"url": "https://a.test/2", "status": "discovered_not_downloaded"})
    state.put_article({"url": "https://a.test/1", "title": "One"})
    state.put_file({"url": "https://a.test/doc.pdf", "path": "files/abc.pdf"})
    state.put_error({"url": "https://a.test/err", "error": "timeout"})

    counts = state.counts()
    assert counts["total_urls"] == 2
    assert counts["completed_urls"] == 1
    assert counts["url_status_extracted"] == 1
    assert counts["url_status_discovered_not_downloaded"] == 1
    assert counts["articles"] == 1
    assert counts["files"] == 1
    assert counts["errors"] == 1

    state.finish("complete", 0, counts, {"requests": 10})
    state.close()

    manifest = json.loads((tmp_path / "output/state/manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["exit_code"] == 0
    assert manifest["counts"]["total_urls"] == 2


def test_iter_pending_scheduled_records_excludes_completed_urls(tmp_path: Path) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    state = CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={},
        runtime_config={},
    )
    state.merge_url(
        {
            "url": "https://a.test/pending",
            "status": "scheduled",
            "frontier_action": "scheduled",
        }
    )
    state.complete_url(
        {
            "url": "https://a.test/done",
            "status": "extracted",
            "frontier_action": "scheduled",
        }
    )
    state.merge_url(
        {
            "url": "https://a.test/disonly",
            "status": "discovered_not_downloaded",
        }
    )

    records = tuple(state.iter_pending_scheduled_records())

    assert [record["url"] for record in records] == ["https://a.test/pending"]
    state.close()


def test_failed_article_transaction_leaves_url_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = open_state(tmp_path)
    state.merge_url({"url": "https://a.test/article", "status": "scheduled"})
    monkeypatch.setattr(state, "_put_payload", fail_disk_write)
    with pytest.raises(OSError, match="disk"):
        state.complete_url(
            {"url": "https://a.test/article", "status": "extracted"},
            article={"url": "https://a.test/article", "text": "Body"},
        )
    assert not state.is_complete("https://a.test/article")


def test_resume_rejects_wrong_state_version(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.close()
    manifest_path = tmp_path / "output/state/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["state_version"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="state version"):
        open_state(tmp_path, resume=True)


def test_finish_publishes_discovery_strategy_to_public_manifest(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="discover")
    discovery = {
        "strategy": "sitemap-only",
        "sitemap_url_limits": "unbounded",
        "hosts": {"a.test": {"mode": "sitemap"}},
    }
    state.finish("complete", 0, {}, {}, discovery=discovery, public_output=tmp_path / "output")
    public = json.loads((tmp_path / "output/manifest.json").read_text())
    assert public["discovery"] == discovery


def test_crawl_manifest_includes_hybrid_discovery(tmp_path: Path) -> None:
    state = open_state(
        tmp_path, phase="crawl", semantic_config={"crawl_strategy": "hybrid-unified"}
    )
    state.finish(
        "complete",
        0,
        state.counts(),
        {},
        discovery={"strategy": "hybrid-unified", "hosts": {}},
        public_output=tmp_path / "public",
    )
    manifest = json.loads((tmp_path / "public/manifest.json").read_text(encoding="utf-8"))
    assert manifest["discovery"]["strategy"] == "hybrid-unified"
    state.close()


def test_asset_referrers_persists_and_updates_file_payload(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p1", "inline_image")
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p2", "inline_image")
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p1", "inline_image")
    assert state.asset_referrers("https://cdn.test/a.png") == (
        "https://a.test/p1",
        "https://a.test/p2",
    )

    # If file row already exists, add_asset_referrer updates its payload
    state.put_file(
        {
            "url": "https://cdn.test/a.png",
            "final_url": "https://cdn.test/a.png",
            "asset_role": "inline_image",
            "referring_pages": ["https://a.test/p1", "https://a.test/p2"],
        }
    )
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p3", "inline_image")
    cursor = state.connection.execute(
        "SELECT payload FROM files WHERE url = ?;", ("https://cdn.test/a.png",)
    )
    updated_payload = json.loads(cursor.fetchone()[0])
    assert updated_payload["referring_pages"] == [
        "https://a.test/p1",
        "https://a.test/p2",
        "https://a.test/p3",
    ]
    state.close()


def test_new_records_are_stamped_with_refresh_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/old", "status": "extracted"})
    state.refresh_run_id = "20261006T000000Z"
    state.upsert_url({"url": "https://a.test/old", "status": "extracted", "etag": '"x"'})
    state.upsert_url({"url": "https://a.test/new", "status": "scheduled"})

    assert "first_seen_run" not in (state.url_record("https://a.test/old") or {})
    assert (state.url_record("https://a.test/new") or {})["first_seen_run"] == "20261006T000000Z"
    state.close()


def test_update_url_fields_keeps_status_and_completion(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    url = "https://a.test/page"
    state.upsert_url({"url": url, "status": "extracted", "duplicate_of": "x"}, completed=True)
    state.update_url_fields(url, {"etag": '"v1"', "duplicate_of": None})
    state.update_url_fields("https://a.test/missing", {"etag": '"v1"'})

    record = state.url_record(url)
    assert record is not None
    assert record["status"] == "extracted"
    assert record["etag"] == '"v1"'
    assert record["duplicate_of"] is None
    assert state.is_complete(url)
    assert state.url_record("https://a.test/missing") is None
    state.close()


def test_release_content_owner_promotes_first_duplicate(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    for url in ("https://a.test/owner", "https://a.test/b", "https://a.test/c"):
        writer.write_article({"url": url, "final_url": url, "text": "Shared text"})
    old_id = str((state.url_record("https://a.test/owner") or {})["content_id"])

    state.release_content_owner("https://a.test/owner", old_id)

    articles = {
        url: json.loads(payload)
        for url, payload in state.connection.execute("SELECT url, payload FROM articles")
    }
    assert articles["https://a.test/b"]["text"] == "Shared text"
    assert "duplicate_of" not in articles["https://a.test/b"]
    assert articles["https://a.test/c"]["duplicate_of"] == "https://a.test/b"
    assert (state.url_record("https://a.test/b") or {})["duplicate_of"] is None
    assert (state.url_record("https://a.test/c") or {})["duplicate_of"] == "https://a.test/b"
    assert state.claim_content(old_id, "https://a.test/z") == "https://a.test/b"
    state.close()


def test_release_content_owner_without_duplicates_frees_content(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    writer.write_article(
        {"url": "https://a.test/a", "final_url": "https://a.test/a", "text": "Only"}
    )
    old_id = str((state.url_record("https://a.test/a") or {})["content_id"])

    state.release_content_owner("https://a.test/a", old_id)

    assert state.claim_content(old_id, "https://a.test/z") == "https://a.test/z"
    state.close()


def test_final_readme_records_crawl_lineage(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.manifest["last_refresh_run_id"] = "20261006T000000Z"
    output = tmp_path / "public"
    state.finish("complete", exit_code=0, counts={}, metrics={}, public_output=output)

    readme = json.loads((output / "final" / "README.json").read_text(encoding="utf-8"))
    assert len(state.crawl_id) == 16
    assert readme["crawl_id"] == state.crawl_id
    assert readme["last_refresh_run_id"] == "20261006T000000Z"
    state.close()


def test_backup_to_copies_the_index(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/", "status": "extracted"})
    target = tmp_path / "archive" / "pre-refresh-x" / "index.sqlite3"

    state.backup_to(target)

    copy = sqlite3.connect(target)
    assert copy.execute("SELECT count(*) FROM url_records").fetchone()[0] == 1
    copy.close()
    state.close()
