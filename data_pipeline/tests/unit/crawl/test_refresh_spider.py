from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scrapy.http import HtmlResponse, Request

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.article import analyze_article
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.refresh import RefreshSession, RefreshSettings
from hust_crawler.crawl.refresh_store import RefreshStore
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


RUN_ID = "20261006T000000Z"
ARTICLE_1 = "<main><h1>Bài một</h1><p>Nội dung bài một của khoa, đủ dài để trích xuất.</p></main>"
ARTICLE_2 = "<main><h1>Bài hai</h1><p>Nội dung bài hai của khoa, đủ dài để trích xuất.</p></main>"
LIST_V1 = '<main><h1>Tin tức</h1><p>Danh sách bài mới.</p><a href="/bai/1">Bài một</a></main>'
LIST_V2 = (
    '<main><h1>Tin tức</h1><p>Danh sách bài mới, đã cập nhật bài hai.</p>'
    '<a href="/bai/2">Bài hai</a><a href="/bai/1">Bài một</a></main>'
)


def refresh_spider(
    writer: CrawlWriter, targets: list[tuple[str, str]], settings: RefreshSettings | None = None
) -> tuple[UnifiedSpider, RefreshStore]:
    store = RefreshStore(writer.state.connection)
    store.start_run(RUN_ID)
    store.add_targets(RUN_ID, targets)
    writer.state.refresh_run_id = RUN_ID
    session = RefreshSession(store, RUN_ID, settings or RefreshSettings())
    return make_spider(writer, refresh=session, refresh_targets=store.pending_targets(RUN_ID)), store


def outcome(store: RefreshStore, url: str) -> str:
    return store.connection.execute(
        "SELECT outcome FROM refresh_targets WHERE run_id = ? AND url = ?;", (RUN_ID, url)
    ).fetchone()[0]


def changes(store: RefreshStore) -> dict[str, str]:
    return {str(c["url"]): str(c["change"]) for c in store.iter_changes(RUN_ID)}


def article_text(writer: CrawlWriter, url: str) -> str:
    payload = writer.state.connection.execute(
        "SELECT payload FROM articles WHERE url = ?;", (url,)
    ).fetchone()[0]
    return str(json.loads(payload).get("text"))


def test_refresh_requests_only_targets_with_validators(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1, etag='"v1"')
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, _ = refresh_spider(writer, [("https://a.test/tin-tuc", "pagination_head")])

    requests = list(spider.start_requests())

    assert [r.url for r in requests] == ["https://a.test/tin-tuc"]
    assert requests[0].headers.get(b"If-None-Match") == b'"v1"'
    assert requests[0].meta["refresh_reason"] == "pagination_head"
    writer.close()


def test_not_modified_target_is_closed_without_expansion(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1, etag='"v1"')
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, "", status=304))) == []
    assert outcome(store, "https://a.test/tin-tuc") == "not_modified"
    assert changes(store) == {}
    writer.close()


def test_unchanged_page_records_no_change_and_fetches_nothing(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, LIST_V1))) == []
    assert outcome(store, "https://a.test/tin-tuc") == "unchanged"
    assert changes(store) == {}
    assert writer.state.url_record("https://a.test/tin-tuc")["last_checked_run"] == RUN_ID
    writer.close()


def test_changed_hub_is_modified_and_only_new_link_is_fetched(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    follow_ups = list(spider.parse_response(page(request, LIST_V2)))

    assert [r.url for r in follow_ups] == ["https://a.test/bai/2"]
    assert writer.state.url_record("https://a.test/bai/2")["first_seen_run"] == RUN_ID
    assert "cập nhật bài hai" in article_text(writer, "https://a.test/tin-tuc")
    list(spider.parse_response(page(follow_ups[0], ARTICLE_2)))
    assert changes(store) == {"https://a.test/bai/2": "new", "https://a.test/tin-tuc": "modified"}
    writer.close()


def test_failed_refetch_keeps_existing_article(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    before = article_text(writer, "https://a.test/tin-tuc")
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, "<h1>Server error</h1>", status=500))) == []

    assert outcome(store, "https://a.test/tin-tuc") == "http_500"
    assert writer.state.url_record("https://a.test/tin-tuc")["status"] == "extracted"
    assert article_text(writer, "https://a.test/tin-tuc") == before
    assert writer.state.connection.execute("SELECT count(*) FROM errors").fetchone()[0] == 0
    writer.close()


def test_soft_404_refetch_keeps_existing_article(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    before = article_text(writer, "https://a.test/tin-tuc")
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    body = "<html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>"
    list(spider.parse_response(page(request, body)))

    assert outcome(store, "https://a.test/tin-tuc").startswith("gate_")
    assert article_text(writer, "https://a.test/tin-tuc") == before
    assert changes(store) == {}
    writer.close()


def test_request_error_on_refresh_target_is_not_a_crawl_error(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    spider.on_request_error(
        SimpleNamespace(request=request, value=TimeoutError("timed out"), type=TimeoutError)
    )

    assert outcome(store, "https://a.test/tin-tuc") == "request_failed"
    assert writer.state.url_record("https://a.test/tin-tuc")["status"] == "extracted"
    assert writer.state.connection.execute("SELECT count(*) FROM errors").fetchone()[0] == 0
    writer.close()


def test_pagination_refetches_known_next_page_only_while_new_links_appear(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    page1_v1 = LIST_V1.replace("</main>", '<a href="/tin-tuc?page=2">2</a></main>')
    page2 = (
        '<main><h1>Tin tức trang 2</h1><p>Danh sách cũ.</p>'
        '<a href="/bai/1">Bài một</a><a href="/tin-tuc?page=3">3</a></main>'
    )
    known_page(writer, "https://a.test/tin-tuc", page1_v1)
    known_page(writer, "https://a.test/tin-tuc?page=2", page2)
    known_page(writer, "https://a.test/tin-tuc?page=3", page2.replace("trang 2", "trang 3"))
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "pagination_head")])
    [request] = list(spider.start_requests())

    follow_ups = list(
        spider.parse_response(
            page(request, LIST_V2.replace("</main>", '<a href="/tin-tuc?page=2">2</a></main>'))
        )
    )

    by_url = {r.url: r for r in follow_ups}
    assert set(by_url) == {"https://a.test/bai/2", "https://a.test/tin-tuc?page=2"}
    assert by_url["https://a.test/tin-tuc?page=2"].meta["refresh_reason"] == "pagination_next"
    assert list(spider.parse_response(page(by_url["https://a.test/tin-tuc?page=2"], page2))) == []
    assert outcome(store, "https://a.test/tin-tuc?page=2") == "unchanged"
    writer.close()
