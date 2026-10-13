from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_pipeline.crawl.state.store import CrawlState


def export_discovery(state: CrawlState, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)

    urls_txt = output / "urls.txt"
    tmp_urls = urls_txt.with_suffix(".tmp")
    cursor = state.connection.execute("SELECT url FROM targets ORDER BY url;")
    with tmp_urls.open("w", encoding="utf-8") as file:
        for (url,) in cursor:
            file.write(f"{url}\n")
    os.replace(tmp_urls, urls_txt)

    errors_jsonl = output / "errors.jsonl"
    tmp_errors = errors_jsonl.with_suffix(".tmp")
    cursor = state.connection.execute("SELECT payload FROM errors ORDER BY url;")
    with tmp_errors.open("w", encoding="utf-8") as file:
        for (payload,) in cursor:
            file.write(f"{payload}\n")
    os.replace(tmp_errors, errors_jsonl)


def export_crawl(state: CrawlState, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)

    urls_txt = output / "urls.txt"
    tmp_urls = urls_txt.with_suffix(".tmp")
    cursor = state.connection.execute(
        """
        SELECT url
        FROM url_records
        WHERE status IN ('extracted', 'file_saved')
        ORDER BY url;
        """
    )
    with tmp_urls.open("w", encoding="utf-8") as file:
        for (url,) in cursor:
            file.write(f"{url}\n")
    os.replace(tmp_urls, urls_txt)

    def _export_table(table: str, target: Path) -> None:
        tmp = target.with_suffix(".tmp")
        cursor = state.connection.execute(f"SELECT payload FROM {table} ORDER BY url;")
        with tmp.open("w", encoding="utf-8") as file:
            for row in cursor:
                file.write(f"{row[0]}\n")
        os.replace(tmp, target)

    _export_table("url_records", output / "url_records.jsonl")
    _export_table("articles", output / "articles.jsonl")
    _export_table("files", output / "files.jsonl")
    _export_table("errors", output / "errors.jsonl")


def export(state: CrawlState, output: Path) -> None:
    export_crawl(state, output)


def iter_final_articles(
    state: CrawlState, urls: Iterable[str] | None = None
) -> Iterator[tuple[str, str]]:
    """Yield (url, payload) for extracted articles, inlining excluded owner text."""
    query = """
        SELECT a.url, a.payload
        FROM articles AS a
        JOIN url_records AS r ON r.url = a.url
        WHERE r.status = 'extracted'
    """
    if urls is None:
        rows: Iterable[tuple[str, str]] = state.connection.execute(query + " ORDER BY a.url;")
    else:

        def selected() -> Iterator[tuple[str, str]]:
            for url in sorted(set(urls)):
                row = state.connection.execute(query + " AND a.url = ?;", (url,)).fetchone()
                if row is not None:
                    yield row

        rows = selected()
    owner_text: dict[str, str] = {}
    for url, payload in rows:
        if '"duplicate_of"' in payload:
            article = json.loads(payload)
            owner_url = article.get("duplicate_of")
            if owner_url:
                owner_status = state.connection.execute(
                    "SELECT status FROM url_records WHERE url = ?", (owner_url,)
                ).fetchone()
                if owner_status is None or owner_status[0] != "extracted":
                    if owner_url not in owner_text:
                        owner_row = state.connection.execute(
                            "SELECT payload FROM articles WHERE url = ?", (owner_url,)
                        ).fetchone()
                        if owner_row is None:
                            raise ValueError(f"missing duplicate owner article: {owner_url}")
                        text = json.loads(owner_row[0]).get("text")
                        if text is None:
                            raise ValueError(f"duplicate owner has no text: {owner_url}")
                        owner_text[owner_url] = str(text)
                    article["text"] = owner_text[owner_url]
                    article.pop("duplicate_of", None)
                    payload = json.dumps(article, ensure_ascii=False)
        yield url, payload


def export_final(state: CrawlState, output: Path) -> None:
    final = output / "final"
    final.mkdir(parents=True, exist_ok=True)
    articles_path = final / "articles.jsonl"
    urls_path = final / "urls.txt"
    tmp_articles = final / "articles.jsonl.tmp"
    tmp_urls = final / "urls.txt.tmp"

    count = 0
    with (
        tmp_articles.open("w", encoding="utf-8") as articles_file,
        tmp_urls.open("w", encoding="utf-8") as urls_file,
    ):
        for url, payload in iter_final_articles(state):
            articles_file.write(f"{payload}\n")
            urls_file.write(f"{url}\n")
            count += 1
    os.replace(tmp_articles, articles_path)
    os.replace(tmp_urls, urls_path)

    summary = {
        "description": "Bộ dữ liệu bài viết đã lọc theo URL crawl thành công",
        "crawl_id": state.crawl_id,
        "source_crawl_finished_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(state.manifest["finished_at"])
        ),
        "article_count": count,
        "url_count": count,
        "files": {
            "articles.jsonl": "Mỗi dòng là một bài viết JSON; chỉ gồm URL trong urls.txt",
            "urls.txt": "Danh sách URL tương ứng, một URL mỗi dòng",
        },
        "crawl_failed_url_count": state.manifest["counts"].get("url_status_failed", 0),
        "note": "Các URL crawl thất bại không nằm trong bộ dữ liệu này.",
    }
    if state.manifest.get("last_refresh_run_id"):
        summary["last_refresh_run_id"] = state.manifest["last_refresh_run_id"]
    tmp_summary = final / "README.json.tmp"
    tmp_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp_summary, final / "README.json")


def asset_metrics(state: CrawlState) -> dict[str, dict[str, int]]:
    from data_pipeline.crawl.frontier.assets import classify_asset

    groups: dict[str, dict[str, int]] = {
        "image": {"file_count": 0, "unique_bytes": 0},
        "document": {"file_count": 0, "unique_bytes": 0},
        "media": {"file_count": 0, "unique_bytes": 0},
    }
    seen_blobs: set[tuple[str, str]] = set()
    rows = state.connection.execute("SELECT payload FROM files").fetchall()
    for (payload_json,) in rows:
        payload = json.loads(payload_json)
        url = payload.get("final_url") or payload.get("url", "")
        content_type = payload.get("content_type", "")
        asset_role = payload.get("asset_role")
        group = payload.get("asset_group") or classify_asset(content_type, url, asset_role)
        if group not in groups:
            group = "document"
        groups[group]["file_count"] += 1
        sha256 = payload.get("sha256")
        size = int(payload.get("size", 0))
        blob_id = (group, sha256) if sha256 else (group, url)
        if blob_id not in seen_blobs:
            seen_blobs.add(blob_id)
            groups[group]["unique_bytes"] += size
    return groups


def reason_counts(state: CrawlState, reasons: frozenset[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    cursor = state.connection.execute("SELECT payload FROM url_records;")
    for row in cursor:
        rec = json.loads(row[0])
        r = rec.get("reason")
        if not r:
            continue
        if reasons is None or r in reasons:
            counts[str(r)] = counts.get(str(r), 0) + 1
    return counts


def skipped_reasons(state: CrawlState) -> dict[str, int]:
    counts: dict[str, int] = {}
    cursor = state.connection.execute("SELECT payload FROM url_records;")
    for row in cursor:
        rec = json.loads(row[0])
        if rec.get("status") == "skipped":
            r = rec.get("reason")
            if r:
                counts[str(r)] = counts.get(str(r), 0) + 1
    return counts
