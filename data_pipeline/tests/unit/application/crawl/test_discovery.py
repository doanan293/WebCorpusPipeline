from pathlib import Path
from typing import Any

import pytest

from data_pipeline.application.crawl.run_discovery import run_discovery
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.seeds import SeedSet
from data_pipeline.domain.crawl.tuning import resolve_runtime_tuning
from data_pipeline.infrastructure.crawl.scrapy import runner as discovery_runner
from data_pipeline.infrastructure.crawl.scrapy.discovery_spider import DiscoverySpider
from data_pipeline.infrastructure.crawl.sqlite.storage import SqliteCrawlStorage
from data_pipeline.infrastructure.crawl.sqlite.unit_of_work import SqliteCrawlUnitOfWork


def capture_state_open(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    observed: dict[str, object] = {}
    original_open = SqliteCrawlUnitOfWork.open

    def fake_open(cls: Any, root: Path, **kwargs: Any) -> SqliteCrawlUnitOfWork:
        observed["root"] = root
        observed.update(kwargs)
        return original_open(root, **kwargs)

    monkeypatch.setattr(SqliteCrawlUnitOfWork, "open", classmethod(fake_open))
    return observed


def run_discovery_for_test(tmp_path: Path) -> int:
    seeds_file = tmp_path / "seeds.txt"
    seeds_file.write_text("https://a.test/explicit\n", encoding="utf-8")
    seeds = SeedSet(
        recursive_hostnames=frozenset(),
        exact_urls=("https://a.test/explicit",),
        invalid=(),
    )
    config = CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.com")

    tuning = resolve_runtime_tuning(
        config=config, concurrency=4, max_per_host=2, resource_limit_percent=80
    )
    output = tmp_path / "output"
    return run_discovery(
        input_path=seeds_file,
        output=output,
        config=config,
        seeds=seeds,
        tuning=tuning,
        resume=False,
        engine=discovery_runner.ScrapyEngine(),
        storage=SqliteCrawlStorage(),
    )


def test_discovery_runner_opens_discover_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeCrawler:
        stats: Any = None

    class FakeCrawlerProcess:
        def __init__(self, *args: Any, **kwargs: Any):
            pass

        def create_crawler(self, *args: Any, **kwargs: Any):
            return FakeCrawler()

        def crawl(self, crawler: Any, **kwargs: Any):
            spider = DiscoverySpider(**kwargs)
            list(spider.start_requests())

        def start(self):
            pass

    monkeypatch.setattr(discovery_runner, "CrawlerProcess", FakeCrawlerProcess)
    observed = capture_state_open(monkeypatch)
    exit_code = run_discovery_for_test(tmp_path)
    assert exit_code == 0
    assert observed["phase"] == "discover"
    assert (tmp_path / "output/urls.txt").exists()
    assert (tmp_path / "output/urls.txt").read_text(encoding="utf-8") == "https://a.test/explicit\n"
