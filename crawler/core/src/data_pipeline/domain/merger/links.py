from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from data_pipeline.domain.merger.link_policy import classify_link
from data_pipeline.domain.shared.assets import recognizable_asset
from data_pipeline.domain.shared.links import normalize_url
from data_pipeline.domain.shared.url import canonicalize_url, classify_frontier_trap

_TRAP_KINDS = frozenset({"search_filter", "calendar_archive", "action_auth"})


def in_scope(host: str, allowed_hosts: frozenset[str]) -> bool:
    """Only the hosts of the seeds, matched exactly: a domain does not cover its subdomains."""
    return host in allowed_hosts


def canonical_url(
    url: str, allowed_ports: frozenset[int] = frozenset()
) -> tuple[str | None, str | None]:
    """The crawl's canonical form; a non-default port only when that port is allowed."""
    canonical, host, reason = canonicalize_url(url)
    if canonical is None and reason == "non_default_port" and urlsplit(url).port in allowed_ports:
        canonical = normalize_url(url)
    return canonical, host


def candidate_urls(
    out_links: Sequence[tuple[str, str]],
    *,
    source_url: str,
    allowed_hosts: frozenset[str],
    allowed_ports: frozenset[int] = frozenset(),
) -> list[str]:
    """Out-links worth crawling: on a seed host, not traps, pages or documents only."""
    seen: set[str] = set()
    kept: list[str] = []
    for raw, text in out_links:
        try:
            link = classify_link(raw, source_url=source_url, text=text)
        except ValueError:
            continue
        if link.kind in _TRAP_KINDS or classify_frontier_trap(link.url) is not None:
            continue
        if link.kind == "asset" and recognizable_asset(link.url) != "document":
            continue
        url, host = canonical_url(link.url, allowed_ports)
        if url is None or host is None or url in seen:
            continue
        if not in_scope(urlsplit(url).hostname or host, allowed_hosts):
            continue
        seen.add(url)
        kept.append(url)
    return kept
