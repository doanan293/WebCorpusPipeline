from __future__ import annotations

from pathlib import Path
from typing import Any

from scrapy.http import HtmlResponse, Request

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.article import analyze_article
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator
from hust_crawler.crawl.spider import UnifiedSpider
from hust_crawler.crawl.state import CrawlState
from hust_crawler.crawl.writer import CrawlWriter

SEEDS = SeedSet(frozenset({"a.test"}), (), ())


def open_writer(tmp_path: Path) -> CrawlWriter:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    options = CrawlOptions()
    state = CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config=options.semantic_snapshot(SEEDS),
        runtime_config={},
    )
    return CrawlWriter(tmp_path, state, options)


def make_spider(writer: CrawlWriter, **kwargs: Any) -> UnifiedSpider:
    frontier = FrontierPolicy(SEEDS, writer.options)
    frontier.restore(writer.state.iter_url_records())
    return UnifiedSpider(
        seeds=SEEDS,
        config=CrawlerConfig(hostnames=SEEDS.allowed_hostnames, contact="ops@example.com"),
        options=writer.options,
        frontier=frontier,
        writer=writer,
        policy=AssetPolicy(writer.options),
        coordinator=SitemapDiscoveryCoordinator(strategy="hybrid-unified"),
        **kwargs,
    )


def page(
    request: Request, body: str, *, status: int = 200, headers: dict[bytes, bytes] | None = None
) -> HtmlResponse:
    return HtmlResponse(
        url=request.url,
        status=status,
        headers={b"Content-Type": b"text/html; charset=utf-8", **(headers or {})},
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def known_page(writer: CrawlWriter, url: str, body: str, **record: object) -> dict[str, Any]:
    """Store url as an extracted page whose text matches what the spider extracts from body."""
    text = str(analyze_article(body.encode("utf-8"), url, "utf-8").article.get("text") or "")
    writer.state.merge_url(
        {
            "url": url,
            "status": "scheduled",
            "frontier_action": "scheduled",
            "response_purpose": "page",
            "discovery_source": "html_link",
            "seed_type": "recursive",
            **record,
        }
    )
    return writer.write_article({"url": url, "final_url": url, "text": text, "page_role": "content"})


def test_html_response_stores_http_validators(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    spider = make_spider(writer)
    request = next(r for r in spider.start_requests() if r.url == "https://a.test/")

    list(
        spider.parse_response(
            page(
                request,
                "<main><h1>Home</h1><p>Welcome to the department news portal.</p></main>",
                headers={b"ETag": b'"v1"', b"Last-Modified": b"Tue, 06 Oct 2026 00:00:00 GMT"},
            )
        )
    )

    record = writer.state.url_record("https://a.test/")
    assert record["status"] == "extracted"
    assert record["etag"] == '"v1"'
    assert record["last_modified"] == "Tue, 06 Oct 2026 00:00:00 GMT"
    writer.close()
