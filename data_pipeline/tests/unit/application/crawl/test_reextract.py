import json
import os
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.application.crawl import reextract as reextract_module
from data_pipeline.application.crawl.reextract import ReextractSummary, reextract
from data_pipeline.application.crawl.writer import CrawlWriter
from data_pipeline.domain.crawl.extract.article import ArticleAnalysis, analyze_article
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.infrastructure.crawl.sqlite.html_store import HtmlStore
from data_pipeline.infrastructure.crawl.sqlite.state_store import SqliteCrawlState
from data_pipeline.infrastructure.crawl.sqlite.storage import SqliteCrawlStorage

PAGES = {
    "https://a.test/a": "<main><h1>A</h1><p>Bài A có nội dung riêng, đủ dài để trích xuất.</p></main>",
    "https://a.test/b": "<main><h1>B</h1><p>Bài B có nội dung chung, đủ dài để trích xuất.</p></main>",
    "https://a.test/c": "<main><h1>B</h1><p>Bài B có nội dung chung, đủ dài để trích xuất.</p></main>",
}


def finished_crawl(tmp_path: Path) -> Path:
    output = tmp_path / "crawl"
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")
    state = SqliteCrawlState.open(
        output / "state",
        phase="crawl",
        input_path=seeds,
        semantic_config={},
        runtime_config={},
    )
    store = HtmlStore(output / "html", state.connection)
    writer = CrawlWriter(output, state, CrawlOptions(), html_store=store)
    for url, html in PAGES.items():
        article = analyze_article(html.encode(), url, "utf-8").article
        writer.write_article({"url": url, "final_url": url, **article, "page_role": "content"})
        writer.store_html(
            url, html.encode(), final_url=url, status=200, content_type="text/html; charset=utf-8"
        )
    state.finish("complete", 0, state.counts(), {}, public_output=output)
    writer.close()
    return output


def articles(output: Path) -> dict[str, dict[str, Any]]:
    state = SqliteCrawlState.open_existing(output / "state")
    try:
        rows = state.connection.execute("SELECT url, payload FROM articles ORDER BY url;")
        return {str(url): json.loads(payload) for url, payload in rows}
    finally:
        state.close()


def test_reextract_applies_new_extractor_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = finished_crawl(tmp_path)

    def marked(source: bytes | str, url: str, encoding: str | None = None) -> ArticleAnalysis:
        analysis = analyze_article(source, url, encoding)
        analysis.article["text"] = f"{analysis.article['text']}\nMARKER"
        return analysis

    monkeypatch.setattr(reextract_module, "analyze_article", marked)

    summary = reextract(output, SqliteCrawlStorage())

    assert summary == ReextractSummary(pages=3, changed=3, duplicates=1)
    stored = articles(output)
    assert stored["https://a.test/c"]["duplicate_of"] == "https://a.test/b"
    owners = [article for article in stored.values() if "duplicate_of" not in article]
    assert all(str(article["text"]).endswith("MARKER") for article in owners)
    final = (output / "final" / "articles.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(final) == 3
    assert all("MARKER" in json.loads(line).get("text", "MARKER") for line in final)


def test_reextract_ownership_is_deterministic(tmp_path: Path) -> None:
    output = finished_crawl(tmp_path)

    reextract(output, SqliteCrawlStorage())
    first = articles(output)
    reextract(output, SqliteCrawlStorage())

    assert articles(output) == first
    assert first["https://a.test/c"]["duplicate_of"] == "https://a.test/b"


def test_reextract_refuses_while_locked(tmp_path: Path) -> None:
    output = finished_crawl(tmp_path)
    (output / "state" / "run.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already running"):
        reextract(output, SqliteCrawlStorage())
