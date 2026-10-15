from __future__ import annotations

import pytest

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.runtime.profiles import resolve_runtime_tuning


def config(**overrides: object) -> CrawlerConfig:
    return CrawlerConfig(
        hostnames=frozenset({"a.test"}),
        contact="ops@example.org",
        **overrides,  # pyrefly: ignore[bad-argument-type]
    )


def test_runtime_tuning_uses_config_values() -> None:
    tuning = resolve_runtime_tuning(
        config=config(
            concurrent_requests=11,
            concurrent_per_host=3,
            resource_limit_percent=65,
            playwright_max_pages=5,
            progress_interval_seconds=4.0,
        )
    )
    assert tuning.max_concurrency == 11
    assert tuning.max_per_host == 3
    assert tuning.resource_limit_percent == 65
    assert tuning.playwright_max_pages == 5
    assert tuning.progress_interval_seconds == 4.0


def test_custom_concurrency_overrides() -> None:
    tuning = resolve_runtime_tuning(
        config=config(), concurrency=32, max_per_host=4, resource_limit_percent=70
    )
    assert tuning.max_concurrency == 32
    assert tuning.max_per_host == 4
    assert tuning.resource_limit_percent == 70


def test_invalid_limits_rejected() -> None:
    with pytest.raises(ValueError, match="concurrency limits must be positive"):
        resolve_runtime_tuning(config=config(), concurrency=0)
    with pytest.raises(ValueError, match="resource limit percent"):
        resolve_runtime_tuning(config=config(), resource_limit_percent=150)
