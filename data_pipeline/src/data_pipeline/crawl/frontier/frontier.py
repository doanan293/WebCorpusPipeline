from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

from data_pipeline.core.policies.url import canonicalize_url, classify_frontier_trap
from data_pipeline.crawl.frontier.assets import AssetGroup, AssetRole
from data_pipeline.crawl.frontier.link_policy import LinkKind, is_navigation_path, query_key_kind
from data_pipeline.crawl.frontier.seeds import SeedMode, SeedSet
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.state.discovery import FamilyObservation, RouteFamilyState

_MIN_PAGINATION_SEGMENTS = 2

DiscoverySource = Literal["seed", "sitemap", "html_link"]
Purpose = Literal["robots", "sitemap", "page", "asset"]


@dataclass(frozen=True, slots=True)
class Candidate:
    url: str
    source_mode: SeedMode
    discovered_from: str | None
    kind: str
    purpose: Purpose
    explicit: bool = False
    discovery_source: DiscoverySource = "seed"
    link_kind: LinkKind | None = None
    family_key: str | None = None
    ordinal: int | None = None
    asset_role: AssetRole | None = None
    asset_group: AssetGroup | None = None


@dataclass(frozen=True, slots=True)
class FrontierDecision:
    action: Literal["schedule", "record_only", "reject"]
    canonical_url: str | None
    reason: str | None
    priority: int


class FrontierPolicy:
    def __init__(
        self,
        seeds: SeedSet,
        options: CrawlOptions,
        trap_exceptions: (
            Mapping[str, tuple[str, ...]] | tuple[tuple[str, tuple[str, ...]], ...] | None
        ) = None,
        route_families: Iterable[RouteFamilyState] = (),
    ) -> None:
        self.seeds = seeds
        self.options = options
        if not trap_exceptions:
            self.trap_exceptions = {}
        elif isinstance(trap_exceptions, Mapping):
            self.trap_exceptions = {k: set(v) for k, v in trap_exceptions.items()}
        else:
            self.trap_exceptions = {k: set(v) for k, v in trap_exceptions}

        self._scheduled_urls: set[str] = set()
        self._total_scheduled: int = 0
        self._host_scheduled: dict[str, int] = defaultdict(int)
        self._query_variants: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._consecutive_empty_pages: dict[tuple[str, str, str], int] = defaultdict(int)
        self._closed_branches: set[tuple[str, str, str]] = set()
        self._route_families: dict[str, RouteFamilyState] = {
            f.family_key: f for f in route_families
        }
        self.stats: dict[str, int] = defaultdict(int)

    def family_is_closed(self, family_key: str) -> bool:
        family = self._route_families.get(family_key)
        return family.closed if family is not None else False

    def observe_family(self, observation: FamilyObservation) -> RouteFamilyState:
        current = self._route_families.get(
            observation.family_key,
            RouteFamilyState(
                family_key=observation.family_key,
                kind=observation.kind,
                scheduled_next_ordinal=observation.ordinal,
                content_fingerprints=(),
                target_fingerprints=(),
                accepted_targets=(),
                consecutive_zero_novelty=0,
                closed=False,
                closure_reason=None,
            ),
        )
        if current.closed:
            return current

        new_targets = tuple(
            sorted(set(observation.content_targets) - set(current.accepted_targets))
        )
        repeated_content = observation.content_fingerprint in current.content_fingerprints
        repeated_targets = observation.target_fingerprint in current.target_fingerprints
        zero_count = current.consecutive_zero_novelty + 1 if not new_targets else 0
        closure_reason = (
            "repeated_content_fingerprint"
            if repeated_content
            else "repeated_target_set"
            if repeated_targets
            else "zero_novelty"
            if zero_count >= self.options.pagination_empty_pages
            else None
        )
        closed = closure_reason is not None

        new_state = RouteFamilyState(
            family_key=observation.family_key,
            kind=observation.kind,
            scheduled_next_ordinal=observation.ordinal,
            content_fingerprints=tuple(
                dict.fromkeys((*current.content_fingerprints, observation.content_fingerprint))
            ),
            target_fingerprints=tuple(
                dict.fromkeys((*current.target_fingerprints, observation.target_fingerprint))
            ),
            accepted_targets=tuple(
                sorted(set(current.accepted_targets) | set(observation.content_targets))
            ),
            consecutive_zero_novelty=zero_count,
            closed=closed,
            closure_reason=closure_reason,
        )
        self._route_families[observation.family_key] = new_state
        return new_state

    def _pagination_branch(self, url: str) -> tuple[str, str, str] | None:
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        has_page_query = any(query_key_kind(k) == "pagination" for k, _ in pairs)
        segments = [s for s in parsed.path.split("/") if s]
        has_page_path = (
            len(segments) >= _MIN_PAGINATION_SEGMENTS
            and segments[-2].lower() in {"page", "p", "pages"}
            and segments[-1].isdigit()
        )

        if not has_page_query and not has_page_path:
            return None

        if has_page_path:
            norm_path = ("/" + "/".join(segments[:-2])) if segments[:-2] else "/"
        else:
            norm_path = parsed.path.rstrip("/") or "/"

        filtered_query = urlencode(
            sorted([(k, v) for k, v in pairs if query_key_kind(k) != "pagination"])
        )
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (hostname, norm_path, filtered_query)

    def observe_pagination(self, page_url: str, new_content_count: int) -> None:
        canonical, _, _ = canonicalize_url(page_url)
        if not canonical:
            return
        branch = self._pagination_branch(canonical)
        if not branch:
            return
        if new_content_count == 0:
            self.stats["frontier/pagination_empty_observation"] += 1
            self._consecutive_empty_pages[branch] += 1
            if self._consecutive_empty_pages[branch] >= self.options.pagination_empty_pages:
                self._closed_branches.add(branch)
        else:
            self._consecutive_empty_pages[branch] = 0

    def page_role(
        self, url: str, link_kind: LinkKind | None = None
    ) -> Literal["content", "navigation"]:
        if link_kind == "pagination":
            return "navigation"
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query_kinds = {query_key_kind(k) for k, _ in pairs}
        if "filter" in query_kinds:
            return "navigation"
        if "pagination" in query_kinds:
            is_wordpress_content_id = link_kind == "content" and all(
                query_key_kind(key) != "pagination" or key.casefold() == "p" for key, _ in pairs
            )
            if not is_wordpress_content_id:
                return "navigation"
        if is_navigation_path(parsed.path):
            return "navigation"
        if link_kind == "content":
            return "content"
        path = parsed.path.rstrip("/")
        if not path:
            return "content"
        segments = [s.lower() for s in path.split("/") if s]
        if (
            len(segments) >= _MIN_PAGINATION_SEGMENTS
            and segments[-2] in {"page", "p", "pages"}
            and segments[-1].isdigit()
        ):
            return "navigation"
        return "content"

    def _scope_rejection(self, candidate: Candidate, hostname: str) -> str | None:
        seeds = self.seeds
        if candidate.explicit:
            return None if hostname in seeds.allowed_hostnames else "host_out_of_scope"
        if candidate.purpose == "asset":
            if candidate.discovered_from:
                _, parent_host, _ = canonicalize_url(candidate.discovered_from)
                in_scope = parent_host is not None and parent_host in seeds.allowed_hostnames
            else:
                in_scope = hostname in seeds.allowed_hostnames
            return None if in_scope else "host_out_of_scope"
        if candidate.source_mode != "recursive":
            return "exact_seed_no_expansion"
        if candidate.discovered_from:
            parent_host = (urlsplit(candidate.discovered_from).hostname or "").lower().rstrip(".")
            in_scope = parent_host in seeds.recursive_hostnames and hostname == parent_host
        else:
            in_scope = hostname in seeds.recursive_hostnames
        return None if in_scope else "host_out_of_scope"

    def _count_rejection(self, reason: str, hostname: str, *, per_host: bool = True) -> None:
        self.stats[f"frontier/reject/{reason}"] += 1
        if per_host:
            self.stats[f"frontier/host/{hostname}/reject/{reason}"] += 1

    def _policy_rejection(
        self, candidate: Candidate, canonical: str, hostname: str, role: str
    ) -> str | None:
        """Return the first budget or trap rejection reason, recording its statistics."""
        if candidate.discovery_source == "html_link":
            trap = classify_frontier_trap(canonical)
            if trap and trap not in self.trap_exceptions.get(hostname, set()):
                self._count_rejection(trap, hostname)
                return trap
        if candidate.purpose != "asset" and role == "navigation":
            branch = self._pagination_branch(canonical)
            if branch and branch in self._closed_branches:
                self._count_rejection("pagination_closed", hostname)
                return "pagination_closed"
        if self._total_scheduled >= self.options.max_total_urls:
            self._count_rejection("total_url_limit", hostname, per_host=False)
            return "total_url_limit"
        if self._host_scheduled[hostname] >= self.options.max_urls_per_host:
            self._count_rejection("host_url_limit", hostname)
            return "host_url_limit"
        return None

    @staticmethod
    def _priority(candidate: Candidate, role: str) -> int:
        # explicit/robots=1000, sitemap=800, content=500, navigation=100
        if candidate.explicit or candidate.source_mode == "exact" or candidate.purpose == "robots":
            return 1000
        if candidate.purpose == "sitemap" or candidate.discovery_source == "sitemap":
            return 800
        if candidate.purpose == "asset" or role == "content":
            return 500
        return 100

    def consider(self, candidate: Candidate) -> FrontierDecision:
        canonical, hostname, reason = canonicalize_url(
            candidate.url,
            check_traps=candidate.discovery_source == "html_link",
        )
        if not canonical:
            return FrontierDecision("reject", None, reason or "malformed_url", 0)
        if hostname is None:
            msg = "canonicalize_url returned a URL without a hostname"
            raise RuntimeError(msg)

        out_of_scope = self._scope_rejection(candidate, hostname)
        if out_of_scope is not None:
            return FrontierDecision("reject", canonical, out_of_scope, 0)

        # Return record_only for canonical URLs already in _scheduled_urls
        if canonical in self._scheduled_urls:
            return FrontierDecision("record_only", canonical, "already_scheduled", 0)

        role = self.page_role(canonical, candidate.link_kind)
        rejection = self._policy_rejection(candidate, canonical, hostname, role)
        if rejection is not None:
            return FrontierDecision("reject", canonical, rejection, 0)

        # Query variants are budgeted only for navigation pages found through html links.
        parsed = urlsplit(canonical)
        charges_query_variant_budget = bool(
            candidate.purpose != "asset"
            and candidate.discovery_source == "html_link"
            and candidate.link_kind != "pagination"
            and role == "navigation"
            and parsed.query
        )
        variants: set[str] | None = None
        if charges_query_variant_budget:
            variants = self._query_variants[(hostname, parsed.path)]
            if (
                parsed.query not in variants
                and len(variants) >= self.options.max_query_variants_per_path
            ):
                self._count_rejection("query_variant_limit", hostname)
                return FrontierDecision("reject", canonical, "query_variant_limit", 0)

        priority = self._priority(candidate, role)

        # Atomically add the canonical URL to all counters, query sets, and stats
        self._scheduled_urls.add(canonical)
        self._total_scheduled += 1
        self._host_scheduled[hostname] += 1
        self.stats["frontier/scheduled"] += 1
        self.stats[f"frontier/host/{hostname}/scheduled"] += 1
        if variants is not None:
            variants.add(parsed.query)

        return FrontierDecision("schedule", canonical, None, priority)

    def restore(self, records: Iterable[Mapping[str, object]]) -> None:
        for record in records:
            if record.get("frontier_action") != "scheduled":
                continue
            url = str(record.get("url") or "")
            if not url or url in self._scheduled_urls:
                continue
            canonical, hostname, _ = canonicalize_url(url)
            if not canonical or not hostname:
                continue
            if canonical in self._scheduled_urls:
                continue
            self._scheduled_urls.add(canonical)
            self._total_scheduled += 1
            self._host_scheduled[hostname] += 1
            parsed = urlsplit(canonical)
            disc_src = record.get("discovery_source")
            link_kind = cast(LinkKind | None, record.get("link_kind"))
            if (
                disc_src == "html_link"
                and link_kind != "pagination"
                and self.page_role(canonical, link_kind) == "navigation"
                and parsed.query
            ):
                path_key = (hostname, parsed.path)
                self._query_variants[path_key].add(parsed.query)

    def snapshot(self) -> dict[str, object]:
        return {
            "total_scheduled": self._total_scheduled,
            "host_scheduled": dict(self._host_scheduled),
            "closed_pagination_branches": len(self._closed_branches),
            "stats": dict(self.stats),
            "route_families": {
                "total": len(self._route_families),
                "closed": sum(state.closed for state in self._route_families.values()),
                "closure_reasons": dict(
                    sorted(
                        Counter(
                            state.closure_reason
                            for state in self._route_families.values()
                            if state.closure_reason
                        ).items()
                    )
                ),
            },
        }
