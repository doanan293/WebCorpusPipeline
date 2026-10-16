from __future__ import annotations

from typing import Any

from scrapy import Spider
from scrapy.crawler import Crawler, CrawlerProcess
from scrapy.settings import Settings

from data_pipeline.crawl.application.ports import EngineResult, SpiderSetup
from data_pipeline.crawl.domain.config import CrawlerConfig
from data_pipeline.crawl.domain.tuning import RuntimeTuning
from data_pipeline.crawl.infrastructure.scrapy.discovery_spider import DiscoverySpider
from data_pipeline.crawl.infrastructure.scrapy.progress import CrawlProgressReporter
from data_pipeline.crawl.infrastructure.scrapy.reuse_spider import ReusedAssetSpider
from data_pipeline.crawl.infrastructure.scrapy.settings import build_settings
from data_pipeline.crawl.infrastructure.scrapy.unified_spider import UnifiedSpider

SPIDERS: dict[str, type[Spider]] = {
    "unified": UnifiedSpider,
    "reuse": ReusedAssetSpider,
    "discovery": DiscoverySpider,
}


class PreparedScrapyCrawl:
    def __init__(self, process: CrawlerProcess, crawler: Crawler, kwargs: dict[str, Any]) -> None:
        self.process = process
        self.crawler = crawler
        self.kwargs = kwargs

    def start(self) -> EngineResult:
        self.process.crawl(self.crawler, **self.kwargs)
        self.process.start()
        stats: dict[str, Any] = self.crawler.stats.get_stats() if self.crawler.stats else {}
        reporter = getattr(self.crawler, "progress_reporter", None)
        return EngineResult(stats, reporter.metrics() if reporter is not None else {})


class ScrapyEngine:
    """The ``CrawlEngine`` port: one Scrapy process running one spider to completion."""

    def prepare(
        self, config: CrawlerConfig, tuning: RuntimeTuning, setup: SpiderSetup
    ) -> PreparedScrapyCrawl:
        extensions: dict[object, int] = {}
        # Discovery runs report no progress: they only probe robots.txt and sitemaps.
        if setup.kind != "discovery" and tuning.progress_interval_seconds > 0:
            extensions[CrawlProgressReporter] = 500
        settings = Settings()
        for key, value in build_settings(config, tuning=tuning, extensions=extensions).items():
            settings.set(key, value, priority="cmdline")
        process = CrawlerProcess(settings=settings)
        return PreparedScrapyCrawl(
            process, process.create_crawler(SPIDERS[setup.kind]), setup.kwargs
        )
