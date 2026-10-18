from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from data_pipeline.domain.aggregator.link_policy import classify_link
from data_pipeline.domain.shared.assets import recognizable_asset
from data_pipeline.domain.shared.url import canonicalize_url, classify_frontier_trap

_TRAP_KINDS = frozenset({"search_filter", "calendar_archive", "action_auth"})


def in_scope(host: str, allowed_hosts: frozenset[str], allowed_suffixes: tuple[str, ...]) -> bool:
    """A listed host, or the domain itself or any subdomain of an allowed suffix."""
    return host in allowed_hosts or any(
        host == suffix or host.endswith(f".{suffix}") for suffix in allowed_suffixes
    )


def candidate_urls(
    out_links: Sequence[tuple[str, str]],
    *,
    source_url: str,
    allowed_hosts: frozenset[str],
    allowed_suffixes: tuple[str, ...],
) -> list[str]:
    """Out-links worth crawling: inside the hust domain, not traps, pages or documents only."""
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
        url, host, _reason = canonicalize_url(link.url)
        if url is None or host is None or url in seen:
            continue
        if not in_scope(urlsplit(url).hostname or host, allowed_hosts, allowed_suffixes):
            continue
        seen.add(url)
        kept.append(url)
    return kept
