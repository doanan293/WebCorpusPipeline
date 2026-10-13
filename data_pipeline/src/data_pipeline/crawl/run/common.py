from __future__ import annotations

from data_pipeline.crawl.frontier.seeds import SeedSet
from data_pipeline.crawl.state.store import CrawlState


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
