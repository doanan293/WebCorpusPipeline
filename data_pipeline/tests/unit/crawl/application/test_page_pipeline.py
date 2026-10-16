from __future__ import annotations

from typing import Any, cast

from data_pipeline.crawl.application.page_pipeline import PagePipeline
from data_pipeline.crawl.application.refresh_pipeline import RefreshSession
from data_pipeline.crawl.domain.frontier.assets import AssetPolicy
from data_pipeline.crawl.domain.frontier.frontier import FrontierPolicy
from data_pipeline.crawl.domain.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.crawl.domain.options import CrawlOptions
from data_pipeline.crawl.domain.pages import FailedRequest, FetchedPage, Scheduled
from data_pipeline.crawl.domain.seeds import SeedSet
from tests.fakes.crawl import FakeCrawlState, FakeCrawlWriter


def pipeline(refresh: RefreshSession | None = None) -> tuple[PagePipeline, FakeCrawlWriter]:
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    options = CrawlOptions()
    writer = FakeCrawlWriter(FakeCrawlState(), options)
    pipe = PagePipeline(
        options=options,
        frontier=FrontierPolicy(seeds, options),
        writer=cast(Any, writer),
        policy=AssetPolicy(options),
        coordinator=SitemapDiscoveryCoordinator(strategy="hybrid-unified"),
        refresh=refresh,
    )
    return pipe, writer


def page(url: str, html: str, **meta: object) -> FetchedPage:
    return FetchedPage(
        url,
        url,
        200,
        "text/html; charset=utf-8",
        html.encode(),
        "utf-8",
        "page",
        "recursive",
        "seed",
        meta,
    )


ARTICLE = (
    "<main><h1>Tin tức</h1><p>Nội dung đủ dài để trích xuất bài viết của trường.</p>"
    '<a href="/bai/1">Bài 1</a><a href="/login">Đăng nhập</a></main>'
)


def test_html_page_is_stored_and_content_links_are_scheduled() -> None:
    pipe, writer = pipeline()

    follow = list(pipe.handle(page("https://a.test/", ARTICLE)))

    assert [a["url"] for a in writer.state.articles] == ["https://a.test/"]
    assert [f.url for f in follow if isinstance(f, Scheduled)] == ["https://a.test/bai/1"]
    assert writer.state.url_records["https://a.test/bai/1"]["status"] == "scheduled"
    assert "https://a.test/" in writer.checkpoints


def test_navigation_link_is_recorded_not_scheduled() -> None:
    pipe, writer = pipeline()

    follow = list(pipe.handle(page("https://a.test/", ARTICLE)))

    assert "https://a.test/login" not in [f.url for f in follow]
    assert writer.state.url_records["https://a.test/login"]["reason"] == "action_auth"


def test_duplicate_page_is_not_expanded() -> None:
    pipe, writer = pipeline()
    writer.next_duplicate_of = "https://a.test/original"

    follow = list(pipe.handle(page("https://a.test/copy", ARTICLE)))

    assert follow == []
    assert writer.checkpoints == ["https://a.test/copy"]


def test_robots_declared_sitemap_is_scheduled_once() -> None:
    pipe, _ = pipeline()
    robots = FetchedPage(
        "https://a.test/robots.txt",
        "https://a.test/robots.txt",
        200,
        "text/plain",
        b"Sitemap: https://a.test/news.xml\nSitemap: https://a.test/news.xml\n",
        "utf-8",
        "robots",
        "recursive",
        "seed",
        {},
    )

    follow = list(pipe.handle(robots))

    assert [f.url for f in follow] == ["https://a.test/news.xml"]


def test_failed_https_homepage_falls_back_to_http() -> None:
    pipe, writer = pipeline()
    failure = FailedRequest(
        url="https://a.test/",
        input_url="https://a.test/",
        seed_mode="recursive",
        purpose="page",
        request_scope="page",
        discovery_source="seed",
        explicit=False,
        http_fallback=False,
        refresh_reason=None,
        error="Connection refused",
        error_type="ConnectionRefusedError",
        ignored=False,
    )

    follow = pipe.handle_failure(failure)

    assert [(f.url, getattr(f, "http_fallback", None)) for f in follow] == [
        ("http://a.test/", True)
    ]
    assert writer.state.url_records["https://a.test/"]["status"] == "failed"
