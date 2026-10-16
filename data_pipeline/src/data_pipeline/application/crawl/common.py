from __future__ import annotations

from urllib.parse import urlsplit

from data_pipeline.application.crawl.ports import UrlRecords
from data_pipeline.domain.crawl.seeds import SeedSet


def exact_only_hosts(seeds: SeedSet) -> frozenset[str]:
    """Hosts that only appear in exact URL seeds (no recursive crawl)."""
    hosts = {(urlsplit(url).hostname or "").lower().rstrip(".") for url in seeds.exact_urls}
    return frozenset(host for host in hosts if host) - seeds.recursive_hostnames


def record_invalid_seeds(seeds: SeedSet, urls: UrlRecords) -> None:
    """Persist every seed line that could not be parsed as a failed URL."""
    for invalid in seeds.invalid:
        urls.put_error(
            {
                "url": invalid.value,
                "line": invalid.line,
                "error": invalid.reason,
                "message": f"line {invalid.line}: {invalid.reason}",
            }
        )
        urls.upsert_url({"url": invalid.value, "status": "failed", "reason": invalid.reason})
