from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[2]))

from scrapy.utils.reactor import install_reactor

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

from scrapy import signals  # noqa: E402

import data_pipeline.crawl.runtime.settings as crawl_settings  # noqa: E402
from data_pipeline.cli import crawl as cli  # noqa: E402
from data_pipeline.crawl.run import full as runner  # noqa: E402
from data_pipeline.crawl.runtime.adaptive import AdaptiveConcurrencyMiddleware  # noqa: E402


class StopAfterResponse:
    target = "https://news.test/bai/n1"

    @classmethod
    def from_crawler(cls, crawler):
        extension = cls(crawler)
        crawler.signals.connect(extension.on_response, signal=signals.response_downloaded)
        return extension

    def __init__(self, crawler):
        self.crawler = crawler

    def on_response(self, response, **kwargs):
        if response.url == self.target:
            self.crawler.engine.close_spider(self.crawler.spider, "shutdown")


def main(root: Path, step: str, version: int, fail_paths: str = "") -> int:
    root.mkdir(parents=True, exist_ok=True)
    seeds_file = root / "seeds.txt"
    if not seeds_file.exists():
        seeds_file.write_text("news.test\n", encoding="utf-8")
    config_file = root / "config.yaml"
    if not config_file.exists():
        values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
        values.update(
            contact="ops@example.org", retry_backoff_base_seconds=0.01, download_delay_seconds=0.001
        )
        config_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    request_log = root / "requested_urls.txt"
    with request_log.open("a", encoding="utf-8") as log:
        log.write(f"# {step}\n")

    original_build_settings = crawl_settings.build_settings

    def fixture_build_settings(cfg, *, state_dir, phase="crawl", tuning=None, extensions=None):
        values = original_build_settings(
            cfg, state_dir=state_dir, phase=phase, tuning=tuning, extensions=extensions
        )
        handler = "tests.fixtures.refresh_site_handler.RefreshSiteHandler"
        values["DOWNLOAD_HANDLERS"] = {"http": handler, "https": handler}
        values["FIXTURE_REQUEST_LOG"] = str(request_log)
        values["FIXTURE_SITE_VERSION"] = version
        values["FIXTURE_FAIL_PATHS"] = fail_paths
        values.setdefault("DOWNLOADER_MIDDLEWARES", {})[AdaptiveConcurrencyMiddleware] = None
        if step == "refresh_interrupt":
            values.setdefault("EXTENSIONS", {})[StopAfterResponse] = 50
        return values

    crawl_settings.build_settings = fixture_build_settings
    runner.build_settings = fixture_build_settings

    argv = [
        "--input",
        str(seeds_file),
        "--output",
        str(root / "crawl"),
        "--config",
        str(config_file),
        "--profile",
        "safe-fast",
        "--concurrency",
        "4",
        "--max-per-host",
        "2",
    ]
    if step != "initial":
        argv.append("--refresh")
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(
        main(
            Path(sys.argv[1]),
            sys.argv[2],
            int(sys.argv[3]),
            sys.argv[4] if len(sys.argv) > 4 else "",
        )
    )
