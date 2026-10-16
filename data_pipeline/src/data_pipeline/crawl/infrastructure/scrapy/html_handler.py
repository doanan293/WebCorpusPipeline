from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import scrapy
from scrapy.http import Response

from data_pipeline.crawl.application.writer import CrawlWriter
from data_pipeline.crawl.domain.access import classify_page_gate
from data_pipeline.crawl.domain.extract.article import analyze_article
from data_pipeline.crawl.domain.frontier.assets import (
    AssetPolicy,
    classify_asset,
    is_recognizable_binary,
    recognizable_asset,
    role_for_group,
)
from data_pipeline.crawl.domain.frontier.frontier import Candidate, DiscoverySource, FrontierPolicy
from data_pipeline.crawl.domain.frontier.link_policy import (
    ClassifiedLink,
    classify_link,
    select_semantic_next,
)
from data_pipeline.crawl.domain.frontier.sitemaps import (
    SitemapDiscoveryCoordinator,
    SitemapParseError,
    declared_sitemap_url,
    parse_sitemap,
)
from data_pipeline.crawl.domain.models import FamilyObservation
from data_pipeline.crawl.domain.options import CrawlOptions
from data_pipeline.crawl.domain.routing import ResponseRoute, route_response
from data_pipeline.crawl.domain.seeds import SeedMode
from data_pipeline.crawl.infrastructure.scrapy.assets import AssetResponseHandler
from data_pipeline.crawl.infrastructure.scrapy.refresh_handler import (
    RefreshSession,
    response_validators,
)
from data_pipeline.crawl.infrastructure.scrapy.requests import RequestFactory
from data_pipeline.crawl.infrastructure.sqlite.discovery_rows import put_route_family
from data_pipeline.crawl.infrastructure.sqlite.state_store import SqliteCrawlState
from data_pipeline.shared.domain.url import canonicalize_url


@dataclass(frozen=True, slots=True)
class _Page:
    """A fetched response together with the request metadata the handlers need."""

    response: Response
    input_url: str
    purpose: str
    seed_mode: SeedMode
    discovery_source: DiscoverySource
    content_type: str
    route: ResponseRoute


_NAVIGATION_KINDS = frozenset({"search_filter", "calendar_archive", "action_auth"})


def _classify_discovery(discovery: Any, source_url: str) -> ClassifiedLink | None:
    try:
        return classify_link(
            discovery["url"],
            source_url=source_url,
            text=discovery.get("text", ""),
            rel=tuple(discovery.get("rel", ())),
            resource_kind_hint=discovery.get("kind"),
        )
    except Exception:  # noqa: BLE001 -- a malformed link must not abort the page
        return None


def _classified_discoveries(discoveries: Iterable[Any], source_url: str) -> list[ClassifiedLink]:
    links = (_classify_discovery(discovery, source_url) for discovery in discoveries)
    return [link for link in links if link is not None and link.kind != "asset"]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


class HtmlHandler:
    """Turns crawl responses into stored records and follow-up requests."""

    def __init__(
        self,
        *,
        options: CrawlOptions,
        frontier: FrontierPolicy,
        writer: CrawlWriter,
        policy: AssetPolicy,
        coordinator: SitemapDiscoveryCoordinator,
        requests: RequestFactory,
        refresh: RefreshSession | None,
        stats: Callable[[], Any],
    ) -> None:
        self.options = options
        self.frontier = frontier
        self.writer = writer
        self.policy = policy
        self.coordinator = coordinator
        self.requests = requests
        self.refresh = refresh
        self._stats = stats
        self.assets = AssetResponseHandler(options=options, writer=writer, policy=policy)
        # Set by the spider in refresh mode to fetch the next pagination page.
        self.pagination_fallback: Callable[[str], scrapy.Request | None] | None = None
        self._route_handlers: dict[str, Callable[[_Page], Iterator[scrapy.Request]]] = {
            "html": self._html,
            "robots": self._robots,
            "sitemap": self._sitemap,
            "optional_absent": self._optional_absent,
            "http_error": self._http_error,
            "file": self._checkpoint_only,
            "transient": self._checkpoint_only,
            "unknown": self._checkpoint_only,
        }

    def handle(self, response: Response) -> Iterator[scrapy.Request]:
        meta = response.meta
        content_type = (response.headers.get(b"Content-Type") or b"").decode(
            "latin1", errors="replace"
        )
        page = _Page(
            response=response,
            input_url=meta.get("input_url", response.url),
            purpose=meta.get("response_purpose", "page"),
            seed_mode=meta.get("seed_mode", "exact"),
            discovery_source=meta.get("discovery_source", "seed"),
            content_type=content_type,
            route=route_response(
                url=response.url,
                status=response.status,
                content_type=content_type,
                purpose=meta.get("response_purpose", "page"),
            ),
        )
        if self.assets.try_handle(response):
            return
        handler = self._route_handlers.get(page.route.action)
        if handler is not None:
            yield from handler(page)

    def _html(self, page: _Page) -> Iterator[scrapy.Request]:
        response = page.response
        analysis = analyze_article(response.body, response.url, getattr(response, "encoding", None))
        article_data = self.article_record(response, analysis, page.input_url, page.content_type)
        stored_article = self._store_article(article_data)
        self.writer.store_html(
            page.input_url,
            response.body,
            final_url=response.url,
            status=response.status,
            content_type=page.content_type,
        )
        self.store_validators(page.input_url, response)
        if self.refresh is not None:
            self.refresh.record_change(
                page.input_url,
                "new",
                page_role=str(article_data["page_role"]),
                content_id=str(stored_article["content_id"]),
            )
        yield from self.expand(response, analysis, stored_article, page.input_url, page.seed_mode)

    def _store_article(self, article_data: dict[str, Any]) -> dict[str, Any]:
        return self.writer.write_article(article_data)

    def _robots(self, page: _Page) -> Iterator[scrapy.Request]:
        response = page.response
        hostname = _host(response.url)
        if page.seed_mode == "recursive":
            encoding = getattr(response, "encoding", None) or "utf-8"
            for line in response.body.decode(encoding, errors="replace").splitlines():
                sitemap_url = declared_sitemap_url(line, response.url, hostname)
                if sitemap_url is None:
                    continue
                if self.coordinator.register_sitemap(hostname, sitemap_url, required=True):
                    candidate = Candidate(
                        sitemap_url,
                        "recursive",
                        response.url,
                        "sitemap",
                        explicit=False,
                        discovery_source="sitemap",
                    )
                    request = self.requests.for_candidate(candidate)
                    if request is not None:
                        yield request
        self.coordinator.complete_robots(hostname)
        self.writer.checkpoint(page.input_url)

    def _sitemap(self, page: _Page) -> Iterator[scrapy.Request]:
        response = page.response
        hostname = _host(response.url)
        try:
            parsed = parse_sitemap(response.body, response.url, page.route.content_type)
        except SitemapParseError:
            self.coordinator.complete_sitemap(hostname, response.url, result="failed")
            self.writer.write_error({"url": page.input_url, "error": "sitemap_parse_error"})
            self.writer.checkpoint(page.input_url)
            return
        if parsed.kind == "index":
            yield from self._child_sitemaps(page, hostname, parsed.locations)
        else:
            yield from self._sitemap_pages(page, hostname, parsed.locations)
        self.coordinator.complete_sitemap(hostname, response.url, result="success")
        self.writer.checkpoint(page.input_url)

    def _child_sitemaps(
        self, page: _Page, hostname: str, locations: Iterable[str]
    ) -> Iterator[scrapy.Request]:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            if self.coordinator.register_sitemap(hostname, loc, required=False):
                candidate = Candidate(
                    loc,
                    page.seed_mode,
                    page.response.url,
                    "sitemap",
                    explicit=False,
                    discovery_source="sitemap",
                )
                request = self.requests.for_candidate(candidate)
                if request is not None:
                    yield request

    def _sitemap_pages(
        self, page: _Page, hostname: str, locations: Iterable[str]
    ) -> Iterator[scrapy.Request]:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            self.coordinator.record_sitemap_target(hostname, loc)
            request = self.requests.for_candidate(
                self._sitemap_candidate(loc, page.response.url, page.seed_mode)
            )
            if request is not None:
                yield request

    @staticmethod
    def _sitemap_candidate(loc: str, source_url: str, seed_mode: SeedMode) -> Candidate:
        group = recognizable_asset(loc)
        if is_recognizable_binary(loc) or group is not None:
            return Candidate(
                loc,
                seed_mode,
                source_url,
                purpose="asset",
                explicit=False,
                discovery_source="sitemap",
                asset_role=role_for_group(group),
                asset_group=group,
            )
        try:
            classified = classify_link(loc, source_url=source_url)
            link_kind, family_key, ordinal = (
                classified.kind,
                classified.family_key,
                classified.ordinal,
            )
        except Exception:  # noqa: BLE001 -- a malformed link must not abort the page
            link_kind = family_key = ordinal = None
        return Candidate(
            loc,
            seed_mode,
            source_url,
            purpose="page",
            explicit=False,
            discovery_source="sitemap",
            link_kind=link_kind,
            family_key=family_key,
            ordinal=ordinal,
        )

    def _optional_absent(self, page: _Page) -> Iterator[scrapy.Request]:
        response = page.response
        hostname = _host(response.url)
        if page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, response.url, result="absent")
        elif page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        self.writer.record_absent(
            {
                "url": page.input_url,
                "final_url": response.url,
                "status": "absent",
                "http_status": response.status,
            }
        )
        self.writer.checkpoint(page.input_url)
        return iter(())

    def _http_error(self, page: _Page) -> Iterator[scrapy.Request]:
        response = page.response
        hostname = _host(response.url)
        if page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, response.url, result="failed")
        elif page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        fallback = self._https_fallback(page)
        if fallback is not None:
            yield fallback
        self.writer.write_error(
            {
                "url": page.input_url,
                "final_url": response.url,
                "error": page.route.error or f"http_{response.status}",
            }
        )

    def _https_fallback(self, page: _Page) -> scrapy.Request | None:
        response = page.response
        if not (
            response.url.startswith("https://")
            and not response.meta.get("http_fallback")
            and page.purpose == "page"
        ):
            return None
        parsed = urlsplit(response.url)
        if parsed.path not in {"", "/"}:
            return None
        candidate = Candidate(
            f"http://{parsed.netloc}/",
            page.seed_mode,
            None,
            "page",
            explicit=response.meta.get("explicit", False),
            discovery_source=page.discovery_source,
        )
        request = self.requests.for_candidate(candidate)
        if request is not None:
            request.meta["http_fallback"] = True
        return request

    def _checkpoint_only(self, page: _Page) -> Iterator[scrapy.Request]:
        self.writer.checkpoint(page.input_url)
        return iter(())

    def expansion_gate_reason(
        self, response: Response, stored_article: dict[str, Any]
    ) -> str | None:
        if stored_article.get("duplicate_of"):
            return "duplicate"

        link_kind = response.meta.get("link_kind")
        if link_kind in {"search_filter", "calendar_archive", "action_auth"}:
            return link_kind

        encoding = getattr(response, "encoding", None) or "utf-8"
        if hasattr(response, "text"):
            html = response.text
        else:
            html = response.body.decode(encoding, errors="replace")

        gate = classify_page_gate(
            status=response.status,
            url=response.url,
            html=html,
            text=str(stored_article.get("text") or ""),
        )
        if not gate.expandable:
            return gate.reason

        return None

    def article_record(
        self, response: Response, analysis: Any, input_url: str, content_type: str
    ) -> dict[str, Any]:
        return {
            "url": input_url,
            "final_url": response.url,
            **analysis.article,
            "page_role": self.frontier.page_role(
                response.url, cast(Any, response.meta.get("link_kind"))
            ),
            "status": response.status,
            "content_type": content_type,
        }

    def store_validators(self, url: str, response: Response, **extra: object) -> None:
        fields: dict[str, object] = {**response_validators(response.headers), **extra}
        if fields:
            self.writer.state.update_url_fields(url, fields)

    def expand(
        self,
        response: Response,
        analysis: Any,
        stored_article: dict[str, Any],
        input_url: str,
        seed_mode: SeedMode,
    ) -> Iterator[scrapy.Request]:
        gate_reason = self.expansion_gate_reason(response, stored_article)
        if gate_reason is not None:
            stats = self._stats()
            if stats is not None:
                stats.inc_value(f"frontier/terminal/{gate_reason}")
            self.writer.checkpoint(input_url)
            return

        yield from self._asset_requests(response, analysis, seed_mode)
        if seed_mode == "recursive":
            yield from self._recursive_expansion(response, analysis, stored_article)
        self.writer.checkpoint(input_url)

    def _asset_requests(
        self, response: Response, analysis: Any, seed_mode: SeedMode
    ) -> Iterator[scrapy.Request]:
        for asset in analysis.assets:
            self.writer.state.add_asset_referrer(asset.url, response.url, asset.role)
            candidate = Candidate(
                url=asset.url,
                source_mode=seed_mode,
                discovered_from=response.url,
                purpose="asset",
                explicit=False,
                discovery_source="html_link",
                asset_role=asset.role,
                asset_group=classify_asset(url=asset.url, asset_role=asset.role),
            )
            request = self.requests.for_candidate(candidate)
            if request is not None:
                yield request

    def _recursive_expansion(
        self, response: Response, analysis: Any, stored_article: dict[str, Any]
    ) -> Iterator[scrapy.Request]:
        own_link = classify_link(response.url, source_url=response.url)
        family_key = response.meta.get("family_key") or own_link.family_key
        ordinal = response.meta.get("ordinal") or own_link.ordinal

        accepted_content_urls: list[str] = []
        new_scheduled_content_count = 0
        pagination_by_family: dict[str, list[ClassifiedLink]] = defaultdict(list)
        for link in _classified_discoveries(analysis.discoveries, response.url):
            if link.kind in _NAVIGATION_KINDS:
                self._record_skipped_link(link)
            elif link.kind == "content":
                new_scheduled_content_count += yield from self._content_link(
                    link, response.url, accepted_content_urls
                )
            elif link.kind == "pagination" and link.family_key:
                pagination_by_family[link.family_key].append(link)

        if family_key and self.refresh is None:
            self._observe_family(stored_article, family_key, ordinal, accepted_content_urls)

        for pagination_key, links in pagination_by_family.items():
            if not self._may_follow_pagination(pagination_key, new_scheduled_content_count):
                continue
            next_link = select_semantic_next(
                links, current_ordinal=ordinal if pagination_key == family_key else None
            )
            if next_link is not None:
                yield from self._next_page_requests(next_link, response.url)

    def _record_skipped_link(self, link: ClassifiedLink) -> None:
        canonical, hostname, _ = canonicalize_url(link.url, check_traps=False)
        if canonical is None or hostname not in self.frontier.seeds.allowed_hostnames:
            return
        self.writer.record_navigation_skip(
            {
                "url": canonical,
                "final_url": canonical,
                "status": "skipped",
                "reason": link.kind,
                "discovery_source": "html_link",
                "response_purpose": "page",
            }
        )

    def _content_link(
        self, link: ClassifiedLink, parent_url: str, accepted_urls: list[str]
    ) -> Generator[scrapy.Request, None, int]:
        """Schedule a content link; return 1 when it adds a new content page to the crawl."""
        candidate = Candidate(
            url=link.url,
            source_mode="recursive",
            discovered_from=parent_url,
            purpose="page",
            explicit=False,
            discovery_source="html_link",
            link_kind="content",
        )
        decision = self.frontier.consider(candidate)
        if decision.action in {"schedule", "record_only"}:
            accepted_urls.append(decision.canonical_url or link.url)
        if decision.action == "schedule":
            is_content = self.frontier.page_role(candidate.url, candidate.link_kind) == "content"
            request = self.requests.for_scheduled(candidate, decision)
            if request is not None:
                yield request
            return int(is_content)
        if decision.action == "reject" and decision.canonical_url:
            self.writer.record_rejected(
                {
                    "url": decision.canonical_url,
                    "final_url": decision.canonical_url,
                    "status": "skipped",
                    "reason": decision.reason,
                    "discovery_source": candidate.discovery_source,
                    "response_purpose": candidate.purpose,
                }
            )
        return 0

    def _observe_family(
        self,
        stored_article: dict[str, Any],
        family_key: str,
        ordinal: int | None,
        accepted_content_urls: list[str],
    ) -> None:
        content_targets = tuple(sorted(set(accepted_content_urls)))
        observation = FamilyObservation(
            family_key=family_key,
            kind="pagination",
            ordinal=ordinal,
            content_fingerprint=str(stored_article.get("content_id") or ""),
            target_fingerprint=hashlib.sha256(
                "\n".join(content_targets).encode("utf-8")
            ).hexdigest(),
            content_targets=content_targets,
        )
        family_state = self.frontier.observe_family(observation)
        crawl_state = getattr(self.writer, "state", None)
        if isinstance(crawl_state, SqliteCrawlState):
            put_route_family(crawl_state, family_state)

    def _may_follow_pagination(self, family_key: str, new_content_count: int) -> bool:
        if self.refresh is not None:
            return self.refresh.follow_pagination(family_key, new_content_count)
        return not self.frontier.family_is_closed(family_key)

    def _next_page_requests(
        self, next_link: ClassifiedLink, parent_url: str
    ) -> Iterator[scrapy.Request]:
        candidate = Candidate(
            url=next_link.url,
            source_mode="recursive",
            discovered_from=parent_url,
            purpose="page",
            explicit=False,
            discovery_source="html_link",
            link_kind=next_link.kind,
            family_key=next_link.family_key,
            ordinal=next_link.ordinal,
        )
        request = self.requests.for_candidate(candidate)
        if request is None and self.refresh is not None and self.pagination_fallback is not None:
            request = self.pagination_fallback(next_link.url)
        if request is not None:
            yield request
