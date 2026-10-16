from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, override

import scrapy
from scrapy.http import Response

from data_pipeline.application.crawl.ports import DiscoveryHandler
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.pages import Probe
from data_pipeline.infrastructure.crawl.scrapy.requests import failed_request, fetched_page


class DiscoverySpider(scrapy.Spider):
    """Converts between Scrapy and the discovery pipeline: probes out, responses in."""

    name = "discovery_spider"

    def __init__(self, *, config: CrawlerConfig, handler: DiscoveryHandler, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.handler = handler

    @override
    async def start(self) -> AsyncIterator[scrapy.Request]:
        for req in self.start_requests():
            yield req

    def start_requests(self) -> Iterator[scrapy.Request]:
        for probe in self.handler.start():
            yield self._request(probe)

    def _request(self, probe: Probe, current: scrapy.Request | None = None) -> scrapy.Request:
        if probe.retry and current is not None:
            meta = dict(current.meta)
            meta["http_fallback"] = True
            meta["input_url"] = probe.url
        else:
            meta = {
                "input_url": probe.url,
                "logical_url": probe.logical_url or probe.url,
                "response_purpose": probe.purpose,
                "discovery_source": "probe" if probe.purpose == "robots" else "sitemap",
                "discovered_from": probe.discovered_from,
                "handle_httpstatus_all": True,
            }
        return scrapy.Request(
            probe.url,
            callback=self.parse_response,
            errback=self.on_request_error,
            meta=meta,
            dont_filter=True,
        )

    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        purpose = response.meta.get(
            "response_purpose",
            "robots" if response.url.endswith("/robots.txt") else "sitemap",
        )
        page = fetched_page(response, purpose=purpose)
        for probe in self.handler.handle(page):
            yield self._request(probe, response.request)

    def on_request_error(self, failure: Any) -> Iterator[scrapy.Request] | None:
        probes = self.handler.handle_failure(failed_request(failure, purpose="sitemap"))
        if probes:
            return iter([self._request(probe, failure.request) for probe in probes])
        return None
