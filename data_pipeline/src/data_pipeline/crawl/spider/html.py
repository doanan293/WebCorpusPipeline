from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import Any, cast
from urllib.parse import urlsplit

import scrapy
from scrapy.http import Response

from data_pipeline.core.policies.access import classify_page_gate
from data_pipeline.crawl.extract.article import analyze_article
from data_pipeline.crawl.extract.links import normalize_url, resource_kind
from data_pipeline.crawl.frontier.assets import (
    AssetPolicy,
    classify_asset,
    is_recognizable_binary,
    recognizable_asset,
)
from data_pipeline.crawl.frontier.frontier import Candidate, FrontierPolicy
from data_pipeline.crawl.frontier.link_policy import (
    ClassifiedLink,
    classify_link,
    select_semantic_next,
)
from data_pipeline.crawl.frontier.sitemaps import (
    SitemapDiscoveryCoordinator,
    SitemapParseError,
    parse_sitemap,
)
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter
from data_pipeline.crawl.spider.assets import AssetResponseHandler
from data_pipeline.crawl.spider.refresh import RefreshSession, response_validators
from data_pipeline.crawl.spider.requests import RequestFactory
from data_pipeline.crawl.spider.routing import route_response
from data_pipeline.crawl.state.discovery import FamilyObservation, put_route_family
from data_pipeline.crawl.state.store import CrawlState


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

    def handle(self, response: Response) -> Iterator[scrapy.Request]:
        input_url = response.meta.get("input_url", response.url)
        purpose = response.meta.get("response_purpose", "page")
        seed_mode = response.meta.get("seed_mode", "exact")
        discovery_source = response.meta.get("discovery_source", "seed")
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1", errors="replace")
        route = route_response(
            url=response.url, status=response.status, content_type=content_type, purpose=purpose
        )
        if self.assets.try_handle(response):
            return

        if route.action == "html":
            analysis = analyze_article(
                response.body, response.url, getattr(response, "encoding", None)
            )
            article_data = self.article_record(response, analysis, input_url, content_type)
            if hasattr(self.writer, "write_article"):
                stored_article = self.writer.write_article(article_data)
            elif hasattr(self.writer, "put_article"):
                self.writer.put_article(article_data)
                stored_article = article_data
            else:
                stored_article = article_data
            self.store_validators(input_url, response)
            if self.refresh is not None:
                self.refresh.record_change(
                    input_url,
                    "new",
                    page_role=str(article_data["page_role"]),
                    content_id=str(stored_article["content_id"]),
                )
            yield from self.expand(response, analysis, stored_article, input_url, seed_mode)

        elif route.action == "robots":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if seed_mode == "recursive":
                encoding = getattr(response, "encoding", None) or "utf-8"
                for line in response.body.decode(encoding, errors="replace").splitlines():
                    key, separator, value = line.partition(":")
                    if not separator or key.strip().lower() != "sitemap":
                        continue
                    normalized = normalize_url(value.strip(), response.url)
                    if normalized is None:
                        continue
                    sm_host = (urlsplit(normalized).hostname or "").lower().rstrip(".")
                    if sm_host == hostname:
                        if self.coordinator.register_sitemap(hostname, normalized, required=True):
                            cand = Candidate(
                                normalized,
                                "recursive",
                                response.url,
                                "file",
                                "sitemap",
                                explicit=False,
                                discovery_source="sitemap",
                            )
                            req = self.requests.for_candidate(cand)
                            if req is not None:
                                yield req
            self.coordinator.complete_robots(hostname)
            self.writer.checkpoint(input_url)

        elif route.action == "sitemap":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            try:
                parsed = parse_sitemap(response.body, response.url, route.content_type)
            except SitemapParseError:
                self.coordinator.complete_sitemap(hostname, response.url, result="failed")
                self.writer.write_error({"url": input_url, "error": "sitemap_parse_error"})
                self.writer.checkpoint(input_url)
                return

            if parsed.kind == "index":
                for loc in parsed.locations:
                    loc_host = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if loc_host == hostname:
                        if self.coordinator.register_sitemap(hostname, loc, required=False):
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                "sitemap",
                                explicit=False,
                                discovery_source="sitemap",
                            )
                            req = self.requests.for_candidate(cand)
                            if req is not None:
                                yield req
                self.coordinator.complete_sitemap(hostname, response.url, result="success")
            else:
                valid_targets = 0
                for loc in parsed.locations:
                    cand_host = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if cand_host == hostname:
                        self.coordinator.record_sitemap_target(hostname, loc)
                        valid_targets += 1
                        is_bin = is_recognizable_binary(loc)
                        group = recognizable_asset(loc)
                        if is_bin or group is not None:
                            role_map = {
                                "image": "inline_image",
                                "media": "media_reference",
                                "document": "document_attachment",
                            }
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                purpose="asset",
                                explicit=False,
                                discovery_source="sitemap",
                                asset_role=role_map.get(group, "document_attachment")
                                if group
                                else "document_attachment",
                                asset_group=group,
                            )
                        else:
                            try:
                                cl = classify_link(loc, source_url=response.url)
                                link_kind = cl.kind
                                family_key = cl.family_key
                                ordinal = cl.ordinal
                            except Exception:  # noqa: BLE001 -- a malformed link must not abort the page
                                link_kind = None
                                family_key = None
                                ordinal = None
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                purpose="page",
                                explicit=False,
                                discovery_source="sitemap",
                                link_kind=link_kind,
                                family_key=family_key,
                                ordinal=ordinal,
                            )
                        req = self.requests.for_candidate(cand)
                        if req is not None:
                            yield req
                self.coordinator.complete_sitemap(hostname, response.url, result="success")

            self.writer.checkpoint(input_url)

        elif route.action == "optional_absent":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if purpose == "sitemap":
                self.coordinator.complete_sitemap(hostname, response.url, result="absent")
            elif purpose == "robots":
                self.coordinator.complete_robots(hostname)
            self.writer.record_absent(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "absent",
                    "http_status": response.status,
                }
            )
            self.writer.checkpoint(input_url)

        elif route.action == "http_error":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if purpose == "sitemap":
                self.coordinator.complete_sitemap(hostname, response.url, result="failed")
            elif purpose == "robots":
                self.coordinator.complete_robots(hostname)

            # Check for HTTPS homepage fallback
            if (
                response.url.startswith("https://")
                and not response.meta.get("http_fallback")
                and purpose == "page"
            ):
                parsed = urlsplit(response.url)
                if parsed.path in {"", "/"}:
                    http_url = f"http://{parsed.netloc}/"
                    cand = Candidate(
                        http_url,
                        seed_mode,
                        None,
                        "html",
                        "page",
                        explicit=response.meta.get("explicit", False),
                        discovery_source=discovery_source,
                    )
                    req = self.requests.for_candidate(cand)
                    if req is not None:
                        req.meta["http_fallback"] = True
                        yield req

            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "error": route.error or f"http_{response.status}",
                }
            )

        elif route.action in {"file", "transient", "unknown"}:
            self.writer.checkpoint(input_url)

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
        state = getattr(self.writer, "state", None)
        if state is None or not hasattr(state, "update_url_fields"):
            return
        fields: dict[str, object] = {**response_validators(response.headers), **extra}
        if fields:
            state.update_url_fields(url, fields)

    def expand(
        self,
        response: Response,
        analysis: Any,
        stored_article: dict[str, Any],
        input_url: str,
        seed_mode: str,
    ) -> Iterator[scrapy.Request]:
        gate_reason = self.expansion_gate_reason(response, stored_article)
        if gate_reason is not None:
            stats = self._stats()
            if stats is not None:
                stats.inc_value(f"frontier/terminal/{gate_reason}")
            self.writer.checkpoint(input_url)
            return

        for asset in analysis.assets:
            if hasattr(self.writer, "state") and hasattr(self.writer.state, "add_asset_referrer"):
                self.writer.state.add_asset_referrer(asset.url, response.url, asset.role)
            cand = Candidate(
                url=asset.url,
                source_mode=seed_mode,
                discovered_from=response.url,
                kind="file",
                purpose="asset",
                explicit=False,
                discovery_source="html_link",
                asset_role=asset.role,
                asset_group=classify_asset(url=asset.url, asset_role=asset.role),
            )
            req = self.requests.for_candidate(cand)
            if req is not None:
                yield req

        if seed_mode == "recursive":
            cl_self = classify_link(response.url, source_url=response.url)
            current_family_key = response.meta.get("family_key") or cl_self.family_key
            current_ordinal = response.meta.get("ordinal") or cl_self.ordinal

            classified_discoveries: list[ClassifiedLink] = []
            for d in analysis.discoveries:
                try:
                    cl = classify_link(
                        d["url"],
                        source_url=response.url,
                        text=d.get("text", ""),
                        rel=tuple(d.get("rel", ())),
                        resource_kind_hint=d.get("kind"),
                    )
                    classified_discoveries.append(cl)
                except Exception:  # noqa: BLE001 -- a malformed link must not abort the page
                    continue

            accepted_content_urls: list[str] = []
            new_scheduled_content_count = 0
            pagination_by_family: dict[str, list[ClassifiedLink]] = defaultdict(list)

            for cl in classified_discoveries:
                if cl.kind == "asset":
                    continue
                elif cl.kind in {"search_filter", "calendar_archive", "action_auth"}:
                    self.writer.record_skipped(
                        {
                            "url": cl.url,
                            "final_url": cl.url,
                            "status": "skipped",
                            "reason": cl.kind,
                            "discovery_source": "html_link",
                            "response_purpose": "page",
                        }
                    )
                elif cl.kind == "content":
                    cand = Candidate(
                        url=cl.url,
                        source_mode="recursive",
                        discovered_from=response.url,
                        kind="html",
                        purpose="page",
                        explicit=False,
                        discovery_source="html_link",
                        link_kind="content",
                    )
                    decision = self.frontier.consider(cand)
                    if decision.action in {"schedule", "record_only"}:
                        accepted_content_urls.append(decision.canonical_url or cl.url)
                    if decision.action == "schedule":
                        if self.frontier.page_role(cand.url, cand.link_kind) == "content":
                            new_scheduled_content_count += 1
                        req = self.requests.for_scheduled(cand, decision)
                        if req is not None:
                            yield req
                    elif decision.action == "reject" and decision.canonical_url:
                        self.writer.record_skipped(
                            {
                                "url": decision.canonical_url,
                                "final_url": decision.canonical_url,
                                "status": "skipped",
                                "reason": decision.reason,
                                "discovery_source": cand.discovery_source,
                                "response_purpose": cand.purpose,
                            }
                        )
                elif cl.kind == "pagination":
                    if cl.family_key:
                        pagination_by_family[cl.family_key].append(cl)

            if current_family_key and self.refresh is None:
                content_fingerprint = str(stored_article.get("content_id") or "")
                content_targets = tuple(sorted(set(accepted_content_urls)))
                target_fingerprint = hashlib.sha256(
                    "\n".join(content_targets).encode("utf-8")
                ).hexdigest()
                observation = FamilyObservation(
                    family_key=current_family_key,
                    kind="pagination",
                    ordinal=current_ordinal,
                    content_fingerprint=content_fingerprint,
                    target_fingerprint=target_fingerprint,
                    content_targets=content_targets,
                )
                family_state = self.frontier.observe_family(observation)
                crawl_state = getattr(self.writer, "state", None)
                if isinstance(crawl_state, CrawlState):
                    put_route_family(crawl_state, family_state)
                self.frontier.observe_pagination(response.url, new_scheduled_content_count)

            for f_key, links in pagination_by_family.items():
                if self.refresh is not None:
                    if not self.refresh.follow_pagination(f_key, new_scheduled_content_count):
                        continue
                elif self.frontier.family_is_closed(f_key):
                    continue
                next_link = select_semantic_next(
                    links,
                    current_ordinal=current_ordinal if f_key == current_family_key else None,
                )
                if next_link is None:
                    continue
                cand = Candidate(
                    url=next_link.url,
                    source_mode="recursive",
                    discovered_from=response.url,
                    kind="html",
                    purpose="page",
                    explicit=False,
                    discovery_source="html_link",
                    link_kind=next_link.kind,
                    family_key=next_link.family_key,
                    ordinal=next_link.ordinal,
                )
                req = self.requests.for_candidate(cand)
                if (
                    req is None
                    and self.refresh is not None
                    and self.pagination_fallback is not None
                ):
                    req = self.pagination_fallback(next_link.url)
                if req is not None:
                    yield req

        self.writer.checkpoint(input_url)
