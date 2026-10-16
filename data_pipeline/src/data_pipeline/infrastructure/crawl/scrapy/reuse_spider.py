from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, override
from urllib.parse import urlsplit

import scrapy
from scrapy.http import Response

from data_pipeline.application.crawl.ports import AssetHandler
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.models import ReusedAssetTarget
from data_pipeline.infrastructure.crawl.scrapy.requests import failed_request, fetched_page


class ReusedAssetSpider(scrapy.Spider):
    """Fetches again the assets a reused crawl referenced; every request is an asset request."""

    name = "hust_reused_assets"

    def __init__(
        self,
        *,
        config: CrawlerConfig,
        handler: AssetHandler,
        targets: Iterable[ReusedAssetTarget],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.handler = handler
        self.targets = targets

    def start_requests(self) -> Iterator[scrapy.Request]:
        for target in self.handler.reused(self.targets):
            hostname = (urlsplit(target.url).hostname or "").lower().rstrip(".")
            yield scrapy.Request(
                target.url,
                callback=self.parse_response,
                errback=self.on_request_error,
                priority=500,
                meta={
                    "allowed_hostnames": frozenset({hostname}),
                    "input_url": target.url,
                    "seed_mode": "exact",
                    "response_purpose": "asset",
                    "discovery_source": "reused_content",
                    "request_scope": "asset",
                    "asset_mode": "all",
                    "asset_group": target.group,
                    "asset_role": target.role,
                    "referring_page": (
                        target.referring_pages[0] if target.referring_pages else None
                    ),
                    "handle_httpstatus_all": True,
                },
                dont_filter=True,
            )

    @override
    async def start(self) -> AsyncIterator[scrapy.Request]:
        for request in self.start_requests():
            yield request

    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        # Reused targets are always asset requests, so no HTML expansion can follow.
        self.handler.try_handle(fetched_page(response))
        return iter(())

    def on_request_error(self, failure: Any) -> None:
        self.handler.handle_failure(failed_request(failure))
