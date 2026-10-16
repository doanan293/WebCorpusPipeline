from __future__ import annotations

from typing import Any, cast

from data_pipeline.application.crawl.discovery_pipeline import DiscoveryPipeline
from data_pipeline.domain.crawl.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.domain.crawl.pages import FetchedPage
from data_pipeline.domain.crawl.seeds import SeedSet
from tests.fakes.crawl import FakeCrawlState, FakeDiscoveryWriter

SEEDS = SeedSet(frozenset({"a.test"}), (), ())


def probe(url: str, body: str, purpose: Any, content_type: str = "text/plain") -> FetchedPage:
    return FetchedPage(
        url,
        url,
        200,
        content_type,
        body.encode(),
        "utf-8",
        purpose,
        "recursive",
        "seed",
        {"logical_url": url},
    )


def make(coordinator: SitemapDiscoveryCoordinator) -> tuple[DiscoveryPipeline, FakeDiscoveryWriter]:
    writer = FakeDiscoveryWriter(FakeCrawlState())
    return DiscoveryPipeline(seeds=SEEDS, coordinator=coordinator, writer=cast(Any, writer)), writer


def test_robots_declares_a_sitemap_that_is_then_probed() -> None:
    coordinator = SitemapDiscoveryCoordinator()
    pipe, writer = make(coordinator)

    follow = list(
        pipe.handle(
            probe("https://a.test/robots.txt", "Sitemap: https://a.test/news.xml\n", "robots")
        )
    )

    assert [p.url for p in follow] == ["https://a.test/news.xml"]
    assert coordinator.snapshot()["hosts"]["a.test"]["pending_sitemaps"] == 1
    assert writer.probes[0]["status"] == "complete"


def test_sitemap_urls_become_targets_not_probes() -> None:
    coordinator = SitemapDiscoveryCoordinator()
    pipe, writer = make(coordinator)
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://a.test/p</loc></url></urlset>"
    )

    follow = list(
        pipe.handle(probe("https://a.test/sitemap.xml", xml, "sitemap", "application/xml"))
    )

    assert follow == []
    assert [t["url"] for t in writer.targets] == ["https://a.test/p"]
    assert coordinator.snapshot()["hosts"]["a.test"]["mode"] == "sitemap"


def test_a_probe_is_requested_once() -> None:
    pipe, _ = make(SitemapDiscoveryCoordinator())
    robots = probe("https://a.test/robots.txt", "Sitemap: https://a.test/s.xml\n", "robots")

    first = list(pipe.handle(robots))
    second = list(pipe.handle(robots))

    assert [p.url for p in first] == ["https://a.test/s.xml"]
    assert second == []
