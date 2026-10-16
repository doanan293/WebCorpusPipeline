from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import yaml

sys.path.insert(0, str(Path(__file__).parents[2]))

from scrapy.utils.reactor import install_reactor

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

from scrapy import signals

import data_pipeline.crawl.infrastructure.scrapy.settings as crawl_settings
from data_pipeline.cli.main import main as cli_main
from data_pipeline.crawl.application import run_crawl as runner
from data_pipeline.crawl.infrastructure.scrapy.adaptive import AdaptiveConcurrencyMiddleware


class StopAfterResponse:
    target = "https://news.test/bai/n1"

    @classmethod
    def from_crawler(cls, crawler: Any):
        extension = cls(crawler)
        crawler.signals.connect(extension.on_response, signal=signals.response_downloaded)
        return extension

    def __init__(self, crawler: Any):
        self.crawler = crawler

    def on_response(self, response: Any, **kwargs: Any):
        if response.url == self.target:
            self.crawler.engine.close_spider(self.crawler.spider, "shutdown")


def main(root: Path, step: str, version: int, fail_paths: str = "") -> int:
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CRAWLER_CONTACT", "ops@example.org")
    seeds_file = root / "seeds.txt"
    if not seeds_file.exists():
        seeds_file.write_text("news.test\n", encoding="utf-8")
    config_file = root / "config.yaml"
    if not config_file.exists():
        values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
        values.update(retry_backoff_base_seconds=0.01, download_delay_seconds=0.001)
        config_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    request_log = root / "requested_urls.txt"
    with request_log.open("a", encoding="utf-8") as log:
        log.write(f"# {step}\n")

    original_build_settings = crawl_settings.build_settings

    def fixture_build_settings(
        cfg: Any,
        *,
        tuning: Any = None,
        extensions: Any = None,
    ):
        values = original_build_settings(cfg, tuning=tuning, extensions=extensions)
        handler = "tests.fixtures.refresh_site_handler.RefreshSiteHandler"
        values["DOWNLOAD_HANDLERS"] = {"http": handler, "https": handler}
        values["FIXTURE_REQUEST_LOG"] = str(request_log)
        values["FIXTURE_SITE_VERSION"] = version
        values["FIXTURE_FAIL_PATHS"] = fail_paths
        values.setdefault("DOWNLOADER_MIDDLEWARES", {})[AdaptiveConcurrencyMiddleware] = None
        if step == "refresh_interrupt":
            values.setdefault("EXTENSIONS", {})[StopAfterResponse] = 50
        return values

    crawl_settings.build_settings = cast(Any, fixture_build_settings)
    runner.build_settings = cast(Any, fixture_build_settings)

    argv = [
        "--input",
        str(seeds_file),
        "--output",
        str(root / "crawl"),
        "--config",
        str(config_file),
        "--concurrency",
        "4",
        "--max-per-host",
        "2",
    ]
    if step != "initial":
        argv.append("--refresh")
    return cli_main(["crawl", "run", *argv])


if __name__ == "__main__":
    raise SystemExit(
        main(
            Path(sys.argv[1]),
            sys.argv[2],
            int(sys.argv[3]),
            sys.argv[4] if len(sys.argv) > 4 else "",
        )
    )
