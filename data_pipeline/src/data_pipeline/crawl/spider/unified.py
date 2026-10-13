from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.extract.links import resource_kind
from data_pipeline.crawl.frontier.assets import (
    AssetPolicy,
    is_recognizable_binary,
    recognizable_asset,
)
from data_pipeline.crawl.frontier.frontier import (
    Candidate,
    FrontierPolicy,
)
from data_pipeline.crawl.frontier.seeds import SeedSet
from data_pipeline.crawl.frontier.sitemaps import (
    SitemapDiscoveryCoordinator,
)
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter
from data_pipeline.crawl.spider.html import HtmlHandler
from data_pipeline.crawl.spider.refresh import RefreshHandler, RefreshSession
from data_pipeline.crawl.spider.requests import RequestFactory


class UnifiedSpider(scrapy.Spider):
    name = "hust_crawl"
    # Subclasses that skip __init__ (ReusedAssetSpider) never run in refresh mode.
    refresh: RefreshSession | None = None
    refresh_handler: RefreshHandler | None = None
    refresh_targets: tuple[tuple[str, str], ...] = ()

    def __init__(
        self,
        *,
        seeds: SeedSet,
        config: CrawlerConfig,
        options: CrawlOptions,
        frontier: FrontierPolicy,
        writer: CrawlWriter,
        policy: AssetPolicy,
        coordinator: SitemapDiscoveryCoordinator,
        resume_records: Iterable[Mapping[str, object]] = (),
        refresh: RefreshSession | None = None,
        refresh_targets: Iterable[tuple[str, str]] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.seeds = seeds
        self.config = config
        self.options = options
        self.frontier = frontier
        self.writer = writer
        self.policy = policy
        self.coordinator = coordinator
        self.resume_records = tuple(resume_records)
        self.refresh = refresh
        self.refresh_targets = tuple(refresh_targets)

        exact_hosts = (
            frozenset(
                (urlsplit(u).hostname or "").lower().rstrip(".")
                for u in seeds.exact_urls
                if urlsplit(u).hostname
            )
            - seeds.recursive_hostnames
        )
        for eh in exact_hosts:
            self.coordinator.register_exact_host(eh)

        self.requests = RequestFactory(
            options=options,
            frontier=frontier,
            writer=writer,
            policy=policy,
            coordinator=coordinator,
            callback=self.parse_response,
            errback=self.on_request_error,
        )
        self.html = HtmlHandler(
            options=options,
            frontier=frontier,
            writer=writer,
            policy=policy,
            coordinator=coordinator,
            requests=self.requests,
            refresh=refresh,
            stats=self._crawler_stats,
        )
        if refresh is not None:
            self.refresh_handler = RefreshHandler(
                session=refresh,
                html=self.html,
                writer=writer,
                options=options,
                callback=self.parse_response,
                errback=self.on_request_error,
            )
            self.html.pagination_fallback = self.refresh_handler.pagination_request

    def _crawler_stats(self) -> Any:
        crawler = getattr(self, "crawler", None)
        return getattr(crawler, "stats", None) if crawler else None

    def start_requests(self) -> Iterator[scrapy.Request]:
        yield from self.requests.resume(self.resume_records)
        if self.refresh is not None:
            for url, reason in self.refresh_targets:
                assert self.refresh_handler is not None
                yield self.refresh_handler.request(url, reason)
            return

        # Recursive hostname seeds (schedule robots, 3 sitemaps, and homepage)
        for hostname in sorted(self.seeds.recursive_hostnames):
            robots_cand = Candidate(
                f"https://{hostname}/robots.txt",
                "recursive",
                None,
                "file",
                "robots",
                explicit=True,
                discovery_source="seed",
            )
            req = self.requests.for_candidate(robots_cand)
            if req is not None:
                yield req

            for sm_name in ("sitemap.xml", "sitemap_index.xml", "sitemap-index.xml"):
                sm_url = f"https://{hostname}/{sm_name}"
                self.coordinator.register_sitemap(hostname, sm_url, required=False)
                sm_cand = Candidate(
                    sm_url,
                    "recursive",
                    None,
                    "file",
                    "sitemap",
                    explicit=True,
                    discovery_source="sitemap",
                )
                req = self.requests.for_candidate(sm_cand)
                if req is not None:
                    yield req

            root_cand = Candidate(
                f"https://{hostname}/",
                "recursive",
                None,
                "html",
                "page",
                explicit=False,
                discovery_source="seed",
            )
            req = self.requests.for_candidate(root_cand)
            if req is not None:
                yield req

        # Exact URL seeds
        for url in self.seeds.exact_urls:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            mode = "recursive" if host in self.seeds.recursive_hostnames else "exact"
            kind = resource_kind(url)
            group = recognizable_asset(url)
            if group is not None or is_recognizable_binary(url):
                role_map = {
                    "image": "inline_image",
                    "media": "media_reference",
                    "document": "document_attachment",
                }
                cand = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    kind=kind,
                    purpose="asset",
                    explicit=True,
                    discovery_source="seed",
                    asset_role=role_map.get(group, "document_attachment")
                    if group
                    else "document_attachment",
                    asset_group=group,
                )
            else:
                cand = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    kind=kind,
                    purpose="page",
                    explicit=True,
                    discovery_source="seed",
                )
            req = self.requests.for_candidate(cand)
            if req is not None:
                yield req

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for req in self.start_requests():
            yield req

    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        if self.refresh_handler is not None and response.meta.get("refresh_reason"):
            yield from self.refresh_handler.handle(response)
        else:
            yield from self.html.handle(response)

    def on_request_error(self, failure: Any) -> Iterator[scrapy.Request] | None:
        request = failure.request
        input_url = request.meta.get("input_url", request.url)
        seed_mode = request.meta.get("seed_mode", "exact")
        purpose = request.meta.get("response_purpose", "page")
        request_scope = request.meta.get("request_scope", "page")
        discovery_source = request.meta.get("discovery_source", "seed")
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")

        error_str = str(failure.value)
        error_name = getattr(failure.type, "__name__", "")

        if self.refresh is not None and request.meta.get("refresh_reason"):
            outcome = (
                "access_gate" if isinstance(failure.value, IgnoreRequest) else "request_failed"
            )
            self.refresh.finish_target(input_url, outcome, error=error_str[:500])
            return None

        # 1. robots_disallowed
        if isinstance(failure.value, IgnoreRequest) and "robots.txt" in error_str.lower():
            self.writer.record_skipped(
                {"url": input_url, "status": "skipped", "reason": "robots_disallowed"}
            )
            self.writer.checkpoint(input_url)
            return None

        # 2. captcha_blocked / login_required
        access_reason = next(
            (
                reason
                for reason in ("captcha_blocked", "login_required")
                if error_str.startswith(f"{reason}:")
            ),
            None,
        )
        if isinstance(failure.value, IgnoreRequest) and access_reason is not None:
            self.writer.record_skipped(
                {"url": input_url, "status": "skipped", "reason": access_reason}
            )
            self.writer.checkpoint(input_url)
            return None

        # 3. Asset-only IgnoreRequest mapping
        if request_scope == "asset" and isinstance(failure.value, IgnoreRequest):
            asset_safety_reasons = {
                "non_public_address",
                "unsupported_scheme",
                "credentials_not_allowed",
                "non_default_port",
                "redirect_without_location",
                "redirect_limit",
                "redirect_loop",
            }
            matched_reason = next((r for r in asset_safety_reasons if r in error_str), None)
            if matched_reason:
                self.writer.record_skipped(
                    {
                        "url": input_url,
                        "final_url": request.url,
                        "status": "skipped",
                        "reason": matched_reason,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                    }
                )
                self.writer.checkpoint(input_url)
                return None

        if request_scope == "asset":
            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": request.url,
                    "error": "request_failed",
                    "message": error_str,
                }
            )
            self.writer.checkpoint(input_url)
            return None

        # 4. Coordinator notification
        if purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, request.url, result="failed", targets=0)
        elif purpose == "robots":
            self.coordinator.complete_robots(hostname)

        # 5. HTTPS transport error fallback
        fallback_requests: list[scrapy.Request] = []
        if request.url.startswith("https://") and not request.meta.get("http_fallback"):
            parsed = urlsplit(request.url)
            is_homepage = purpose == "page" and parsed.path in {"", "/"}
            is_robots = purpose == "robots" or parsed.path == "/robots.txt"
            is_sitemap = purpose == "sitemap"

            if is_homepage or is_robots or is_sitemap:
                http_url = f"http://{parsed.netloc}{parsed.path}"
                kind = "file" if (is_robots or is_sitemap) else "html"
                cand = Candidate(
                    http_url,
                    seed_mode,
                    None,
                    kind,
                    purpose,
                    explicit=request.meta.get("explicit", False),
                    discovery_source=discovery_source,
                )
                req = self.requests.for_candidate(cand)
                if req is not None:
                    req.meta["http_fallback"] = True
                    fallback_requests.append(req)

        # 6. File size / generic error
        if "MaxSizeExceeded" in error_name or "file_size_limit" in error_str:
            reason = "file_size_limit"
        else:
            reason = "request_failed"
        self.writer.write_error({"url": input_url, "error": reason, "message": error_str})

        if fallback_requests:
            return iter(fallback_requests)
        return None
