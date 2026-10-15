from __future__ import annotations

from urllib.parse import urlsplit

from data_pipeline.crawl.frontier.seeds import SeedSet
from data_pipeline.crawl.state.store import CrawlState


def exact_only_hosts(seeds: SeedSet) -> frozenset[str]:
    """Hosts that only appear in exact URL seeds (no recursive crawl)."""
    hosts = {(urlsplit(url).hostname or "").lower().rstrip(".") for url in seeds.exact_urls}
    return frozenset(host for host in hosts if host) - seeds.recursive_hostnames


def record_invalid_seeds(seeds: SeedSet, state: CrawlState) -> None:
    """Persist every seed line that could not be parsed as a failed URL."""
    for invalid in seeds.invalid:
        state.put_error(
            {
                "url": invalid.value,
                "line": invalid.line,
                "error": invalid.reason,
                "message": f"line {invalid.line}: {invalid.reason}",
            }
        )
        state.upsert_url({"url": invalid.value, "status": "failed", "reason": invalid.reason})
