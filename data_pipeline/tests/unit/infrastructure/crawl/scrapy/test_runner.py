from __future__ import annotations

from typing import Any

import pytest

from data_pipeline.application.crawl.ports import SpiderSetup
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.tuning import resolve_runtime_tuning
from data_pipeline.infrastructure.crawl.scrapy import runner


def test_engine_builds_the_spider_for_the_setup_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self, settings: Any) -> None:
            seen["settings"] = settings

        def create_crawler(self, spider_cls: Any) -> Any:
            seen["spider"] = spider_cls
            return type("Crawler", (), {"stats": None})()

        def crawl(self, crawler: Any, **kwargs: Any) -> None:
            seen["kwargs"] = kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(runner, "CrawlerProcess", FakeProcess)
    config = CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.com")

    prepared = runner.ScrapyEngine().prepare(
        config, resolve_runtime_tuning(config=config), SpiderSetup("reuse", {"targets": ()})
    )
    assert seen["spider"].__name__ == "ReusedAssetSpider"
    assert "kwargs" not in seen  # nothing is crawled until start()

    result = prepared.start()

    assert seen["spider"].__name__ == "ReusedAssetSpider"
    assert seen["kwargs"] == {"targets": ()}
    assert result.stats == {}
    assert result.progress_metrics == {}
