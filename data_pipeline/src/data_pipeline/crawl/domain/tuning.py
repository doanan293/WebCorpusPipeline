from __future__ import annotations

from dataclasses import dataclass

from data_pipeline.crawl.domain.config import MAX_PERCENT, CrawlerConfig


@dataclass(frozen=True, slots=True)
class RuntimeTuning:
    max_concurrency: int
    max_per_host: int
    resource_limit_percent: int
    playwright_max_pages: int
    progress_interval_seconds: float


def resolve_runtime_tuning(
    *,
    config: CrawlerConfig,
    concurrency: int | None = None,
    max_per_host: int | None = None,
    resource_limit_percent: int | None = None,
) -> RuntimeTuning:
    """Apply CLI overrides to the configured performance knobs."""
    tuning = RuntimeTuning(
        max_concurrency=concurrency if concurrency is not None else config.concurrent_requests,
        max_per_host=max_per_host if max_per_host is not None else config.concurrent_per_host,
        resource_limit_percent=(
            resource_limit_percent
            if resource_limit_percent is not None
            else config.resource_limit_percent
        ),
        playwright_max_pages=config.playwright_max_pages,
        progress_interval_seconds=config.progress_interval_seconds,
    )
    if tuning.max_concurrency <= 0 or tuning.max_per_host <= 0:
        msg = "concurrency limits must be positive"
        raise ValueError(msg)
    if not 1 <= tuning.resource_limit_percent <= MAX_PERCENT:
        msg = "resource limit percent must be between 1 and 100"
        raise ValueError(msg)
    return tuning
