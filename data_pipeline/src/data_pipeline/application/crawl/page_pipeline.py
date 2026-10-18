from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from data_pipeline.application.crawl.asset_responses import AssetResponses
from data_pipeline.application.crawl.ports import UrlRecords
from data_pipeline.application.crawl.writer import CrawlWriter
from data_pipeline.domain.aggregator.link_policy import (
    ClassifiedLink,
    classify_link,
    select_semantic_next,
)
from data_pipeline.domain.crawl.frontier.assets import AssetPolicy
from data_pipeline.domain.crawl.frontier.frontier import (
    Candidate,
    DiscoverySource,
    FrontierDecision,
    FrontierPolicy,
    Purpose,
)
from data_pipeline.domain.crawl.frontier.sitemaps import (
    SitemapDiscoveryCoordinator,
    SitemapParseError,
    declared_sitemap_url,
    parse_sitemap,
)
from data_pipeline.domain.crawl.models import FamilyObservation
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.pages import (
    FailedRequest,
    FetchedPage,
    FollowUp,
    RefreshFetch,
    Scheduled,
)
from data_pipeline.domain.crawl.routing import ResponseRoute, route_response
from data_pipeline.domain.crawl.seeds import SeedMode, SeedSet
from data_pipeline.domain.fetcher.access import classify_page_gate
from data_pipeline.domain.parser.extract.article import analyze_article
from data_pipeline.domain.shared.assets import (
    classify_asset,
    is_recognizable_binary,
    recognizable_asset,
    role_for_group,
)
from data_pipeline.domain.shared.url import canonicalize_url

if TYPE_CHECKING:
    from data_pipeline.application.crawl.refresh_pipeline import RefreshPipeline, RefreshSession

_NAVIGATION_KINDS = frozenset({"search_filter", "calendar_archive", "action_auth"})
_SITEMAP_NAMES = ("sitemap.xml", "sitemap_index.xml", "sitemap-index.xml")


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


class PagePipeline:
    """Turns fetched pages into stored records and the follow-up fetches the crawl schedules."""

    def __init__(
        self,
        *,
        options: CrawlOptions,
        frontier: FrontierPolicy,
        writer: CrawlWriter,
        policy: AssetPolicy,
        coordinator: SitemapDiscoveryCoordinator,
        refresh: RefreshSession | None = None,
        stats: Callable[[], Any] = lambda: None,
    ) -> None:
        self.options = options
        self.frontier = frontier
        self.writer = writer
        self.policy = policy
        self.coordinator = coordinator
        self.refresh = refresh
        # The crawler's stats collector, once the engine has one; None until then.
        self.stats = stats
        self.assets = AssetResponses(options=options, writer=writer, policy=policy)
        self._refetch: RefreshPipeline | None = None
        if refresh is not None:
            from data_pipeline.application.crawl.refresh_pipeline import RefreshPipeline  # noqa: PLC0415 -- refresh builds on this module

            self._refetch = RefreshPipeline(refresh, self)
        self._route_handlers: dict[
            str, Callable[[FetchedPage, ResponseRoute], Iterator[FollowUp]]
        ] = {
            "html": self._html,
            "robots": self._robots,
            "sitemap": self._sitemap,
            "optional_absent": self._optional_absent,
            "http_error": self._http_error,
            "file": self._checkpoint_only,
            "transient": self._checkpoint_only,
            "unknown": self._checkpoint_only,
        }

    @property
    def urls(self) -> UrlRecords:
        return self.writer.urls

    @property
    def refreshing(self) -> bool:
        return self.refresh is not None

    # -- scheduling ---------------------------------------------------------------------------

    def scheduled(self, candidate: Candidate, decision: FrontierDecision) -> Scheduled | None:
        """Record an accepted candidate as scheduled; None when the URL is already finished."""
        canonical = decision.canonical_url
        if canonical is None:
            msg = "a scheduled decision must carry a canonical URL"
            raise ValueError(msg)

        # Skip completed URLs on resume
        if self.writer.urls.is_finished(canonical):
            return None

        # Persist scheduled decision
        self.writer.record_scheduled(
            {
                "url": canonical,
                "status": "scheduled",
                "seed_type": candidate.source_mode,
                "discovered_from": candidate.discovered_from,
                "frontier_action": "scheduled",
                "discovery_source": candidate.discovery_source,
                "response_purpose": candidate.purpose,
                "explicit": candidate.explicit,
                "link_kind": candidate.link_kind,
                "family_key": candidate.family_key,
                "ordinal": candidate.ordinal,
                "asset_role": candidate.asset_role,
                "asset_group": candidate.asset_group,
                "priority": decision.priority,
            }
        )
        if candidate.purpose == "page":
            self.coordinator.record_html_target(_host(canonical), canonical)
        return Scheduled(candidate, canonical, decision.priority)

    def _reject(self, decision: FrontierDecision, candidate: Candidate, purpose: str) -> None:
        if decision.canonical_url:
            self.writer.record_rejected(
                {
                    "url": decision.canonical_url,
                    "final_url": decision.canonical_url,
                    "status": "skipped",
                    "reason": decision.reason,
                    "discovery_source": candidate.discovery_source,
                    "response_purpose": purpose,
                }
            )

    def schedule(self, candidate: Candidate) -> Scheduled | None:
        """Ask the frontier about a candidate; record the outcome and return it if it is fetched."""
        if candidate.purpose in {"robots", "sitemap"}:
            decision = self.frontier.consider(candidate)
            if decision.action == "reject":
                self._reject(decision, candidate, candidate.purpose)
                return None
            return self.scheduled(candidate, decision)

        is_binary = is_recognizable_binary(candidate.url)
        is_asset = candidate.purpose == "asset" or is_binary

        if is_asset:
            if self.options.assets == "content-only":
                decision = self.frontier.consider(candidate)
                if decision.canonical_url and decision.action in {"schedule", "record_only"}:
                    self.writer.record_discovered(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "discovered_not_downloaded",
                            "seed_type": candidate.source_mode,
                            "discovered_from": candidate.discovered_from,
                            "content_type": None,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": "asset",
                            "asset_role": candidate.asset_role,
                        }
                    )
                elif decision.action == "reject":
                    self._reject(decision, candidate, "asset")
                return None
            if (
                is_binary
                and recognizable_asset(candidate.url) is None
                and candidate.purpose != "asset"
            ):
                decision = self.frontier.consider(candidate)
                if decision.canonical_url:
                    self.writer.record_rejected(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "skipped",
                            "reason": "unsupported_asset_type",
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                return None

        decision = self.frontier.consider(candidate)
        if decision.action == "reject":
            self._reject(decision, candidate, candidate.purpose)
            return None

        if decision.action == "record_only":
            return None

        return self.scheduled(candidate, decision)

    def resume(self, records: Iterable[Mapping[str, object]]) -> Iterator[Scheduled]:
        """Schedule again the URLs a previous run queued and never finished."""
        for record in records:
            url = str(record["url"])
            purpose_value = str(record.get("response_purpose") or "page")
            purpose = cast(
                Purpose,
                purpose_value
                if purpose_value in {"robots", "sitemap", "page", "asset"}
                else "page",
            )
            source_value = str(record.get("discovery_source") or "html_link")
            discovery_source = cast(
                DiscoverySource,
                source_value if source_value in {"seed", "sitemap", "html_link"} else "html_link",
            )
            candidate = Candidate(
                url=url,
                source_mode="exact" if record.get("seed_type") == "exact" else "recursive",
                discovered_from=(
                    str(record["discovered_from"])
                    if record.get("discovered_from") is not None
                    else None
                ),
                purpose=purpose,
                explicit=bool(record.get("explicit", False)),
                discovery_source=discovery_source,
                link_kind=cast(Any, record.get("link_kind")),
                family_key=(
                    str(record["family_key"]) if record.get("family_key") is not None else None
                ),
                ordinal=cast(int | None, record.get("ordinal")),
                asset_role=cast(Any, record.get("asset_role")),
                asset_group=cast(Any, record.get("asset_group")),
            )
            decision = FrontierDecision(
                "schedule", url, None, int(cast("int | str", record.get("priority") or 0))
            )
            scheduled = self.scheduled(candidate, decision)
            if scheduled is not None:
                yield scheduled

    def start(self, seeds: SeedSet) -> Iterator[Scheduled]:
        """The seed fetches: robots, sitemaps and homepage per host, then exact URLs."""
        for hostname in sorted(seeds.recursive_hostnames):
            robots = Candidate(
                f"https://{hostname}/robots.txt",
                "recursive",
                None,
                "robots",
                explicit=True,
                discovery_source="seed",
            )
            yield from self._optional(self.schedule(robots))
            for name in _SITEMAP_NAMES:
                sitemap_url = f"https://{hostname}/{name}"
                self.coordinator.register_sitemap(hostname, sitemap_url, required=False)
                sitemap = Candidate(
                    sitemap_url,
                    "recursive",
                    None,
                    "sitemap",
                    explicit=True,
                    discovery_source="sitemap",
                )
                yield from self._optional(self.schedule(sitemap))
            root = Candidate(
                f"https://{hostname}/",
                "recursive",
                None,
                "page",
                explicit=False,
                discovery_source="seed",
            )
            yield from self._optional(self.schedule(root))

        for url in seeds.exact_urls:
            mode: SeedMode = "recursive" if _host(url) in seeds.recursive_hostnames else "exact"
            group = recognizable_asset(url)
            if group is not None or is_recognizable_binary(url):
                candidate = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    purpose="asset",
                    explicit=True,
                    discovery_source="seed",
                    asset_role=role_for_group(group),
                    asset_group=group,
                )
            else:
                candidate = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    purpose="page",
                    explicit=True,
                    discovery_source="seed",
                )
            yield from self._optional(self.schedule(candidate))

    @staticmethod
    def _optional(scheduled: Scheduled | None) -> Iterator[Scheduled]:
        if scheduled is not None:
            yield scheduled

    # -- responses ----------------------------------------------------------------------------

    def handle(self, page: FetchedPage) -> Iterator[FollowUp]:
        """Handle a fetched page: a refresh refetch, an asset, or a crawl page."""
        if self._refetch is not None and page.meta.get("refresh_reason"):
            yield from self._refetch.handle(page)
        else:
            yield from self.process(page)

    def process(self, page: FetchedPage) -> Iterator[FollowUp]:
        route = route_response(
            url=page.url, status=page.status, content_type=page.content_type, purpose=page.purpose
        )
        if self.assets.try_handle(page):
            return
        handler = self._route_handlers.get(route.action)
        if handler is not None:
            yield from handler(page, route)

    def _html(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[FollowUp]:
        analysis = analyze_article(page.body, page.url, page.encoding)
        article_data = self.article_record(page, analysis, page.input_url, page.content_type)
        stored_article = self.writer.write_article(article_data)
        self.writer.store_html(
            page.input_url,
            page.body,
            final_url=page.url,
            status=page.status,
            content_type=page.content_type,
        )
        self.store_validators(page.input_url, page)
        if self.refresh is not None:
            self.refresh.record_change(
                page.input_url,
                "new",
                page_role=str(article_data["page_role"]),
                content_id=str(stored_article["content_id"]),
            )
        yield from self.expand(page, analysis, stored_article, page.input_url, page.seed_mode)

    def _robots(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[FollowUp]:
        hostname = _host(page.url)
        if page.seed_mode == "recursive":
            encoding = page.encoding or "utf-8"
            for line in page.body.decode(encoding, errors="replace").splitlines():
                sitemap_url = declared_sitemap_url(line, page.url, hostname)
                if sitemap_url is None:
                    continue
                if self.coordinator.register_sitemap(hostname, sitemap_url, required=True):
                    candidate = Candidate(
                        sitemap_url,
                        "recursive",
                        page.url,
                        "sitemap",
                        explicit=False,
                        discovery_source="sitemap",
                    )
                    yield from self._optional(self.schedule(candidate))
        self.coordinator.complete_robots(hostname)
        self.writer.checkpoint(page.input_url)

    def _sitemap(self, page: FetchedPage, route: ResponseRoute) -> Iterator[FollowUp]:
        hostname = _host(page.url)
        try:
            parsed = parse_sitemap(page.body, page.url, route.content_type)
        except SitemapParseError:
            self.coordinator.complete_sitemap(hostname, page.url, result="failed")
            self.writer.write_error({"url": page.input_url, "error": "sitemap_parse_error"})
            self.writer.checkpoint(page.input_url)
            return
        if parsed.kind == "index":
            yield from self._child_sitemaps(page, hostname, parsed.locations)
        else:
            yield from self._sitemap_pages(page, hostname, parsed.locations)
        self.coordinator.complete_sitemap(hostname, page.url, result="success")
        self.writer.checkpoint(page.input_url)

    def _child_sitemaps(
        self, page: FetchedPage, hostname: str, locations: Iterable[str]
    ) -> Iterator[FollowUp]:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            if self.coordinator.register_sitemap(hostname, loc, required=False):
                candidate = Candidate(
                    loc,
                    page.seed_mode,
                    page.url,
                    "sitemap",
                    explicit=False,
                    discovery_source="sitemap",
                )
                yield from self._optional(self.schedule(candidate))

    def _sitemap_pages(
        self, page: FetchedPage, hostname: str, locations: Iterable[str]
    ) -> Iterator[FollowUp]:
        for loc in locations:
            if _host(loc) != hostname:
                continue
            self.coordinator.record_sitemap_target(hostname, loc)
            yield from self._optional(
                self.schedule(self._sitemap_candidate(loc, page.url, page.seed_mode))
            )

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

    def _optional_absent(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[FollowUp]:
        hostname = _host(page.url)
        if page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, page.url, result="absent")
        elif page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        self.writer.record_absent(
            {
                "url": page.input_url,
                "final_url": page.url,
                "status": "absent",
                "http_status": page.status,
            }
        )
        self.writer.checkpoint(page.input_url)
        return iter(())

    def _http_error(self, page: FetchedPage, route: ResponseRoute) -> Iterator[FollowUp]:
        hostname = _host(page.url)
        if page.purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, page.url, result="failed")
        elif page.purpose == "robots":
            self.coordinator.complete_robots(hostname)
        fallback = self._https_fallback(page)
        if fallback is not None:
            yield fallback
        self.writer.write_error(
            {
                "url": page.input_url,
                "final_url": page.url,
                "error": route.error or f"http_{page.status}",
            }
        )

    def _https_fallback(self, page: FetchedPage) -> Scheduled | None:
        if not (
            page.url.startswith("https://")
            and not page.meta.get("http_fallback")
            and page.purpose == "page"
        ):
            return None
        parsed = urlsplit(page.url)
        if parsed.path not in {"", "/"}:
            return None
        candidate = Candidate(
            f"http://{parsed.netloc}/",
            page.seed_mode,
            None,
            "page",
            explicit=page.meta.get("explicit", False),
            discovery_source=page.discovery_source,
        )
        scheduled = self.schedule(candidate)
        return replace(scheduled, http_fallback=True) if scheduled is not None else None

    def _checkpoint_only(self, page: FetchedPage, _route: ResponseRoute) -> Iterator[FollowUp]:
        self.writer.checkpoint(page.input_url)
        return iter(())

    def expansion_gate_reason(
        self, page: FetchedPage, stored_article: dict[str, Any]
    ) -> str | None:
        if stored_article.get("duplicate_of"):
            return "duplicate"

        link_kind = page.meta.get("link_kind")
        if link_kind in {"search_filter", "calendar_archive", "action_auth"}:
            return link_kind

        gate = classify_page_gate(
            status=page.status,
            url=page.url,
            html=page.text(),
            text=str(stored_article.get("text") or ""),
        )
        if not gate.expandable:
            return gate.reason

        return None

    def article_record(
        self, page: FetchedPage, analysis: Any, input_url: str, content_type: str
    ) -> dict[str, Any]:
        return {
            "url": input_url,
            "final_url": page.url,
            **analysis.article,
            "page_role": self.frontier.page_role(page.url, cast(Any, page.meta.get("link_kind"))),
            "status": page.status,
            "content_type": content_type,
        }

    def store_validators(self, url: str, page: FetchedPage, **extra: object) -> None:
        fields: dict[str, object] = {**page.validators, **extra}
        if fields:
            self.writer.urls.update_url_fields(url, fields)

    def expand(
        self,
        page: FetchedPage,
        analysis: Any,
        stored_article: dict[str, Any],
        input_url: str,
        seed_mode: SeedMode,
    ) -> Iterator[FollowUp]:
        gate_reason = self.expansion_gate_reason(page, stored_article)
        if gate_reason is not None:
            stats = self.stats()
            if stats is not None:
                stats.inc_value(f"frontier/terminal/{gate_reason}")
            self.writer.checkpoint(input_url)
            return

        yield from self._asset_candidates(page, analysis, seed_mode)
        if seed_mode == "recursive":
            yield from self._recursive_expansion(page, analysis, stored_article)
        self.writer.checkpoint(input_url)

    def _asset_candidates(
        self, page: FetchedPage, analysis: Any, seed_mode: SeedMode
    ) -> Iterator[FollowUp]:
        for asset in analysis.assets:
            self.writer.assets.add_asset_referrer(asset.url, page.url, asset.role)
            candidate = Candidate(
                url=asset.url,
                source_mode=seed_mode,
                discovered_from=page.url,
                purpose="asset",
                explicit=False,
                discovery_source="html_link",
                asset_role=asset.role,
                asset_group=classify_asset(url=asset.url, asset_role=asset.role),
            )
            yield from self._optional(self.schedule(candidate))

    def _recursive_expansion(
        self, page: FetchedPage, analysis: Any, stored_article: dict[str, Any]
    ) -> Iterator[FollowUp]:
        own_link = classify_link(page.url, source_url=page.url)
        family_key = page.meta.get("family_key") or own_link.family_key
        ordinal = page.meta.get("ordinal") or own_link.ordinal

        accepted_content_urls: list[str] = []
        new_scheduled_content_count = 0
        pagination_by_family: dict[str, list[ClassifiedLink]] = defaultdict(list)
        for link in _classified_discoveries(analysis.discoveries, page.url):
            if link.kind in _NAVIGATION_KINDS:
                self._record_skipped_link(link)
            elif link.kind == "content":
                new_scheduled_content_count += yield from self._content_link(
                    link, page.url, accepted_content_urls
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
                yield from self._next_page(next_link, page.url)

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
    ) -> Generator[FollowUp, None, int]:
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
            yield from self._optional(self.scheduled(candidate, decision))
            return int(is_content)
        if decision.action == "reject":
            self._reject(decision, candidate, candidate.purpose)
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
        self.writer.families.put_route_family(self.frontier.observe_family(observation))

    def _may_follow_pagination(self, family_key: str, new_content_count: int) -> bool:
        if self.refresh is not None:
            return self.refresh.follow_pagination(family_key, new_content_count)
        return not self.frontier.family_is_closed(family_key)

    def _next_page(self, next_link: ClassifiedLink, parent_url: str) -> Iterator[FollowUp]:
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
        scheduled = self.schedule(candidate)
        if scheduled is not None:
            yield scheduled
        elif self.refresh is not None:
            refetch = self.pagination_refetch(next_link.url)
            if refetch is not None:
                yield refetch

    def pagination_refetch(self, url: str) -> RefreshFetch | None:
        """In a refresh, refetch a known next page (the frontier does not schedule it again)."""
        if self.refresh is None:
            return None
        canonical, _, _ = canonicalize_url(url)
        if not canonical:
            return None
        record = self.writer.urls.url_record(canonical)
        if record is None or record.get("status") != "extracted":
            return None
        if not self.refresh.add_pagination_target(canonical):
            return None
        return RefreshFetch(canonical, "pagination_next")

    # -- failures -----------------------------------------------------------------------------

    def handle_failure(self, failure: FailedRequest) -> list[FollowUp]:
        """Record a request that got no response; return the fallback fetches to try."""
        if self.refresh is not None and failure.refresh_reason:
            outcome = "access_gate" if failure.ignored else "request_failed"
            self.refresh.finish_target(failure.input_url, outcome, error=failure.error[:500])
            return []
        if self.assets.handle_failure(failure):
            return []

        hostname = _host(failure.url)
        purpose = failure.purpose
        # 3. Coordinator notification
        if purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, failure.url, result="failed")
        elif purpose == "robots":
            self.coordinator.complete_robots(hostname)

        # 4. HTTPS transport error fallback
        fallback: list[FollowUp] = []
        if failure.url.startswith("https://") and not failure.http_fallback:
            parsed = urlsplit(failure.url)
            is_homepage = purpose == "page" and parsed.path in {"", "/"}
            is_robots = purpose == "robots" or parsed.path == "/robots.txt"
            is_sitemap = purpose == "sitemap"
            if is_homepage or is_robots or is_sitemap:
                candidate = Candidate(
                    f"http://{parsed.netloc}{parsed.path}",
                    failure.seed_mode,
                    None,
                    purpose,
                    explicit=failure.explicit,
                    discovery_source=failure.discovery_source,
                )
                scheduled = self.schedule(candidate)
                if scheduled is not None:
                    fallback.append(replace(scheduled, http_fallback=True))

        # 6. File size / generic error
        if "MaxSizeExceeded" in failure.error_type or "file_size_limit" in failure.error:
            reason = "file_size_limit"
        else:
            reason = "request_failed"
        self.writer.write_error(
            {"url": failure.input_url, "error": reason, "message": failure.error}
        )
        return fallback
