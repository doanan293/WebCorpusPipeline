from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import scrapy

from data_pipeline.crawl.extract.links import resource_kind
from data_pipeline.crawl.frontier.assets import (
    AssetPolicy,
    is_recognizable_binary,
    recognizable_asset,
)
from data_pipeline.crawl.frontier.frontier import (
    Candidate,
    DiscoverySource,
    FrontierDecision,
    FrontierPolicy,
    Purpose,
)
from data_pipeline.crawl.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter


class RequestFactory:
    """Builds Scrapy requests for frontier candidates and resumed crawl records."""

    def __init__(
        self,
        *,
        options: CrawlOptions,
        frontier: FrontierPolicy,
        writer: CrawlWriter,
        policy: AssetPolicy,
        coordinator: SitemapDiscoveryCoordinator,
        callback: Callable[..., Any],
        errback: Callable[..., Any],
    ) -> None:
        self.options = options
        self.frontier = frontier
        self.writer = writer
        self.policy = policy
        self.coordinator = coordinator
        self.callback = callback
        self.errback = errback

    def for_scheduled(
        self, candidate: Candidate, decision: FrontierDecision
    ) -> scrapy.Request | None:
        canonical = decision.canonical_url
        if canonical is None:
            msg = "a scheduled decision must carry a canonical URL"
            raise ValueError(msg)

        # Skip completed URLs on resume
        state = getattr(self.writer, "state", None)
        if state is not None and hasattr(state, "is_complete") and state.is_complete(canonical):
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

        req_host = (urlsplit(canonical).hostname or "").lower().rstrip(".")

        # Call coordinator.record_html_target for scheduled page candidates
        if candidate.purpose == "page":
            self.coordinator.record_html_target(req_host, canonical)

        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({req_host}),
            "input_url": canonical,
            "seed_mode": candidate.source_mode,
            "explicit": candidate.explicit,
            "response_purpose": candidate.purpose,
            "discovery_source": candidate.discovery_source,
            "discovered_from": candidate.discovered_from,
            "handle_httpstatus_all": True,
            "request_scope": "asset" if candidate.purpose == "asset" else "page",
            "asset_mode": self.options.assets,
            "link_kind": candidate.link_kind,
            "family_key": candidate.family_key,
            "ordinal": candidate.ordinal,
        }
        if candidate.purpose == "asset":
            meta.update(
                {
                    "asset_group": candidate.asset_group
                    or recognizable_asset(canonical)
                    or "document",
                    "asset_role": candidate.asset_role,
                    "referring_page": candidate.discovered_from,
                }
            )

        return scrapy.Request(
            canonical,
            callback=self.callback,
            errback=self.errback,
            priority=decision.priority,
            meta=meta,
            dont_filter=True,
        )

    def for_candidate(self, candidate: Candidate) -> scrapy.Request | None:
        if candidate.purpose in {"robots", "sitemap"}:
            decision = self.frontier.consider(candidate)
            if decision.action == "reject":
                if decision.canonical_url:
                    self.writer.record_skipped(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "skipped",
                            "reason": decision.reason,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": candidate.purpose,
                        }
                    )
                return None
            return self.for_scheduled(candidate, decision)

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
                elif decision.action == "reject" and decision.canonical_url:
                    self.writer.record_skipped(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "skipped",
                            "reason": decision.reason,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                return None
            if (
                is_binary
                and recognizable_asset(candidate.url) is None
                and candidate.purpose != "asset"
            ):
                decision = self.frontier.consider(candidate)
                if decision.canonical_url:
                    self.writer.record_skipped(
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
            if decision.canonical_url:
                self.writer.record_skipped(
                    {
                        "url": decision.canonical_url,
                        "final_url": decision.canonical_url,
                        "status": "skipped",
                        "reason": decision.reason,
                        "discovery_source": candidate.discovery_source,
                        "response_purpose": candidate.purpose,
                    }
                )
            return None

        if decision.action == "record_only":
            return None

        return self.for_scheduled(candidate, decision)

    def resume(self, records: Iterable[Mapping[str, object]]) -> Iterator[scrapy.Request]:
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
                kind=resource_kind(url),
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
            request = self.for_scheduled(candidate, decision)
            if request is not None:
                yield request
