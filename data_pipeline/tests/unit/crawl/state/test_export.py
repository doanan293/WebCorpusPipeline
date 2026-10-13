import json
from pathlib import Path

from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter
from data_pipeline.crawl.state.export import (
    asset_metrics,
    export,
    export_crawl,
    iter_final_articles,
    reason_counts,
)
from tests.unit.crawl.state.test_store import open_state


def test_url_upsert_keeps_one_authoritative_disposition(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/x", "status": "discovered_not_downloaded"})
    state.upsert_url({"url": "https://a.test/x", "status": "extracted"})
    state.checkpoint("https://a.test/x")
    export(state, tmp_path / "output")
    rows = [
        json.loads(line)
        for line in (tmp_path / "output/url_records.jsonl").read_text().splitlines()
    ]
    assert rows == [{"status": "extracted", "url": "https://a.test/x"}]
    assert state.is_complete("https://a.test/x")
    state.close()


def test_crawl_export_lists_only_successful_urls_in_text_file(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    records = (
        {"url": "https://a.test/z-file.pdf", "status": "file_saved"},
        {"url": "https://a.test/a-article", "status": "extracted"},
        {"url": "https://a.test/discovered", "status": "discovered_not_downloaded"},
        {"url": "https://a.test/skipped", "status": "skipped"},
        {"url": "https://a.test/failed", "status": "failed"},
        {"url": "https://a.test/pending", "status": "scheduled"},
    )
    for record in records:
        state.upsert_url(record)

    export_crawl(state, tmp_path / "output")

    assert (tmp_path / "output/urls.txt").read_text(encoding="utf-8") == (
        "https://a.test/a-article\nhttps://a.test/z-file.pdf\n"
    )
    state.close()


def test_final_disposition_preserves_seed_provenance(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.merge_url(
        {
            "url": "https://a.test/article",
            "status": "scheduled",
            "seed_type": "recursive",
            "discovered_from": "https://a.test/",
        }
    )
    state.complete_url(
        {
            "url": "https://a.test/article",
            "status": "extracted",
            "http_status": 200,
        },
        article={"url": "https://a.test/article", "title": "Article", "text": "Body"},
    )
    export(state, tmp_path / "output")
    record = json.loads((tmp_path / "output/url_records.jsonl").read_text())
    assert record["seed_type"] == "recursive"
    assert record["discovered_from"] == "https://a.test/"
    assert state.is_complete(record["url"])


def test_storage_budget_is_a_truncation_reason(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.merge_url(
        {"url": "https://a.test/large.pdf", "status": "skipped", "reason": "storage_budget"},
        completed=True,
    )
    assert reason_counts(state, frozenset({"storage_budget"})) == {"storage_budget": 1}


def test_manifest_groups_stored_bytes_by_asset_class(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.put_file(
        {
            "url": "https://a.test/photo.jpg",
            "final_url": "https://a.test/photo.jpg",
            "content_type": "image/jpeg",
            "size": 5,
            "sha256": "sha-img",
            "asset_role": "inline_image",
        }
    )
    state.put_file(
        {
            "url": "https://a.test/report.pdf",
            "final_url": "https://a.test/report.pdf",
            "content_type": "application/pdf",
            "size": 9,
            "sha256": "sha-pdf",
            "asset_role": "document_attachment",
        }
    )
    # Another URL sharing the same sha256 blob (aliased file)
    state.put_file(
        {
            "url": "https://a.test/photo_alias.jpg",
            "final_url": "https://a.test/photo_alias.jpg",
            "content_type": "image/jpeg",
            "size": 5,
            "sha256": "sha-img",
            "asset_role": "inline_image",
        }
    )
    metrics = asset_metrics(state)
    assert metrics == {
        "image": {"file_count": 2, "unique_bytes": 5},
        "document": {"file_count": 1, "unique_bytes": 9},
        "media": {"file_count": 0, "unique_bytes": 0},
    }
    state.close()


def test_iter_final_articles_limits_to_requested_urls(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    for url, text in (("https://a.test/a", "A"), ("https://a.test/b", "B")):
        writer.write_article({"url": url, "final_url": url, "text": text})

    rows = list(iter_final_articles(state, ["https://a.test/b", "https://a.test/missing"]))

    assert [url for url, _ in rows] == ["https://a.test/b"]
    assert json.loads(rows[0][1])["text"] == "B"
    state.close()
