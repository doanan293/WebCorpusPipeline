from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Literal
from urllib.parse import urlsplit

from data_pipeline.crawl.application.discovery_writer import DiscoveryWriter
from data_pipeline.crawl.domain.frontier.sitemaps import (
    SitemapDiscoveryCoordinator,
    SitemapParseError,
    declared_sitemap_url,
    parse_sitemap,
)
from data_pipeline.crawl.domain.pages import FailedRequest, FetchedPage, Probe
from data_pipeline.crawl.domain.routing import ResponseRoute, route_response
from data_pipeline.crawl.domain.seeds import SeedSet
from data_pipeline.shared.domain.url import canonicalize_url

_SITEMAP_NAMES = ("sitemap.xml", "sitemap_index.xml", "sitemap-index.xml")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


class DiscoveryPipeline:
    """Sitemap-only discovery: probes robots.txt and sitemaps and records their URLs as targets."""

    def __init__(
        self,
        *,
        seeds: SeedSet,
        coordinator: SitemapDiscoveryCoordinator,
        writer: DiscoveryWriter,
    ) -> None:
        self.seeds = seeds
        self.coordinator = coordinator
        self.writer = writer
        self._scheduled_urls: set[str] = set()
        self._route_handlers: dict[str, Callable[[FetchedPage, ResponseRoute], Iterator[Probe]]] = {
            "robots": self._robots,
            "sitemap": self._sitemap,
            "optional_absent": self._absent,
            "http_error": self._http_error,
        }

    def start(self) -> Iterator[Probe]:
        for raw_url in self.seeds.exact_urls:
            canonical, _, _ = canonicalize_url(raw_url, check_traps=False)
            if canonical:
                self.writer.record_target({"url": canonical, "discovered_from": "seed"})
                host = _host(canonical)
                if host and host not in self.seeds.recursive_hostnames:
                    self.coordinator.register_exact_host(host)

        for hostname in sorted(self.seeds.recursive_hostnames):
            robots = self._probe(f"https://{hostname}/robots.txt", purpose="robots")
            if robots is not None:
                yield robots
            for name in _SITEMAP_NAMES:
                candidate = f"https://{hostname}/{name}"
                self.coordinator.register_sitemap(hostname, candidate, required=False)
                sitemap = self._probe(candidate, purpose="sitemap")
                if sitemap is not None:
                    yield sitemap

    def _probe(
        self,
        url: str,
        *,
        purpose: Literal["robots", "sitemap"],
        logical_url: str | None = None,
        discovered_from: str | None = None,
    ) -> Probe | None:
        canonical, _, _ = canonicalize_url(url, check_traps=False)
        target_url = canonical or url
        if target_url in self._scheduled_urls:
            return None
        self._scheduled_urls.add(target_url)
        if self.writer.state.is_complete(target_url):
            return None
        return Probe(target_url, purpose, logical_url or target_url, discovered_from)

    def _http_retry(
        self, request_url: str, *, http_fallback: bool, purpose: Literal["robots", "sitemap"]
    ) -> Probe | None:
        if request_url.startswith("https://") and not http_fallback:
            http_url = f"http://{request_url[len('https://') :]}"
            if http_url in self._scheduled_urls:
                return None
            self._scheduled_urls.add(http_url)
            return Probe(http_url, purpose, retry=True)
        return None

    def handle(self, page: FetchedPage) -> Iterator[Probe]:
        route = route_response(
            url=page.url, status=page.status, content_type=page.content_type, purpose=page.purpose
        )
        handler = self._route_handlers.get(route.action)
        if handler is not None:
            yield from handler(page, route)

    @staticmethod
    def _purpose(page: FetchedPage) -> Literal["robots", "sitemap"]:
        return "robots" if page.purpose == "robots" else "sitemap"

    def _logical_url(self, page: FetchedPage) -> str:
        return str(page.meta.get("logical_url", page.input_url))

    def _record_probe(self, page: FetchedPage, status: str) -> None:
        self.writer.record_probe(
            {
                "url": page.input_url,
                "final_url": page.url,
                "status": status,
                "http_status": page.status,
            }
        )
        self.writer.checkpoint(page.input_url)

    def _robots(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[Probe]:
        hostname = _host(page.url)
        encoding = page.encoding or "utf-8"
        for line in page.body.decode(encoding, errors="replace").splitlines():
            sitemap_url = declared_sitemap_url(line, page.url, hostname)
            if sitemap_url and self.coordinator.register_sitemap(
                hostname, sitemap_url, required=True
            ):
                probe = self._probe(sitemap_url, purpose="sitemap", discovered_from=page.url)
                if probe is not None:
                    yield probe
        self.coordinator.complete_robots(hostname)
        self._record_probe(page, "complete")

    def _sitemap(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[Probe]:
        hostname = _host(page.url)
        logical_url = self._logical_url(page)
        try:
            parsed = parse_sitemap(page.body, page.url, page.content_type)
        except SitemapParseError as exc:
            self.writer.write_error(
                {
                    "url": page.input_url,
                    "final_url": page.url,
                    "error": "invalid_sitemap",
                    "message": str(exc),
                }
            )
            self.coordinator.complete_sitemap(hostname, logical_url, result="failed")
            return
        if parsed.kind == "index":
            yield from self._child_sitemaps(page, hostname, parsed.locations)
        else:
            self._record_targets(page, hostname, parsed.locations)
        self.coordinator.complete_sitemap(hostname, logical_url, result="success")
        self._record_probe(page, "complete")

    def _child_sitemaps(
        self, page: FetchedPage, hostname: str, locations: Iterable[str]
    ) -> Iterator[Probe]:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            if self.coordinator.register_sitemap(hostname, loc, required=True):
                probe = self._probe(loc, purpose="sitemap", discovered_from=page.url)
                if probe is not None:
                    yield probe

    def _record_targets(self, page: FetchedPage, hostname: str, locations: Iterable[str]) -> None:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            canonical, _, _ = canonicalize_url(loc, check_traps=False)
            if canonical:
                self.coordinator.record_sitemap_target(hostname, canonical)
                self.writer.record_target({"url": canonical, "discovered_from": page.url})

    def _absent(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[Probe]:
        hostname = _host(page.url)
        if page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        elif page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, self._logical_url(page), result="absent")
        self._record_probe(page, "absent")
        return iter(())

    def _http_error(self, page: FetchedPage, route: ResponseRoute) -> Iterator[Probe]:
        hostname = _host(page.url)
        retry = (
            self._http_retry(
                page.request_url,
                http_fallback=bool(page.meta.get("http_fallback")),
                purpose=self._purpose(page),
            )
            if page.request_url is not None
            else None
        )
        if retry is not None:
            yield retry
            return
        if page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        elif page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, self._logical_url(page), result="failed")
        self.writer.write_error(
            {
                "url": page.input_url,
                "final_url": page.url,
                "error": route.error or f"http_{page.status}",
            }
        )

    def handle_failure(self, failure: FailedRequest) -> list[Probe]:
        purpose: Literal["robots", "sitemap"] = (
            "robots" if failure.purpose == "robots" else "sitemap"
        )
        retry = self._http_retry(failure.url, http_fallback=failure.http_fallback, purpose=purpose)
        if retry is not None:
            return [retry]

        logical_url = str(failure.meta.get("logical_url", failure.input_url))
        hostname = _host(failure.url)
        if failure.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        elif failure.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, logical_url, result="failed")
        self.writer.write_error(
            {"url": failure.input_url, "error": "request_failed", "message": failure.error}
        )
        return []
