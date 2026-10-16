from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from typing import Any, override

import scrapy
from scrapy.http import Response

from data_pipeline.application.crawl.ports import CrawlHandler
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.seeds import SeedSet
from data_pipeline.infrastructure.crawl.scrapy.requests import (
    RequestFactory,
    failed_request,
    fetched_page,
)


class UnifiedSpider(scrapy.Spider):
    """Converts between Scrapy and the crawl pipeline: requests out, pages and failures in."""

    name = "hust_crawl"

    def __init__(
        self,
        *,
        seeds: SeedSet,
        config: CrawlerConfig,
        handler: CrawlHandler,
        resume_records: Iterable[Mapping[str, object]] = (),
        refresh_targets: Iterable[tuple[str, str]] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.seeds = seeds
        self.config = config
        self.handler = handler
        self.resume_records = tuple(resume_records)
        self.refresh_targets = tuple(refresh_targets)
        handler.stats = self._crawler_stats
        self.requests = RequestFactory(
            options=handler.options,
            urls=handler.urls,
            callback=self.parse_response,
            errback=self.on_request_error,
        )

    def _crawler_stats(self) -> Any:
        crawler = getattr(self, "crawler", None)
        return getattr(crawler, "stats", None) if crawler else None

    def start_requests(self) -> Iterator[scrapy.Request]:
        for scheduled in self.handler.resume(self.resume_records):
            yield self.requests.build(scheduled)
        if self.handler.refreshing:
            for url, reason in self.refresh_targets:
                yield self.requests.refresh(url, reason)
            return
        for scheduled in self.handler.start(self.seeds):
            yield self.requests.build(scheduled)

    @override
    async def start(self) -> AsyncIterator[scrapy.Request]:
        for req in self.start_requests():
            yield req

    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        for follow in self.handler.handle(fetched_page(response)):
            yield self.requests.build(follow)

    def on_request_error(self, failure: Any) -> Iterator[scrapy.Request] | None:
        fallbacks = self.handler.handle_failure(failed_request(failure))
        if fallbacks:
            return iter([self.requests.build(follow) for follow in fallbacks])
        return None
