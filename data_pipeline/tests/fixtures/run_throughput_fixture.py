from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parents[2]))

from scrapy.utils.reactor import install_reactor

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

import data_pipeline.crawl.infrastructure.scrapy.settings as crawl_settings
from data_pipeline.cli.main import main as cli_main
from data_pipeline.crawl.application import run_crawl as runner

HOSTS = ("h1.test", "h2.test")


def main(root: Path) -> int:
    """Crawl two generated hosts with the repo's config/crawler.yaml; no network is used."""
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CRAWLER_CONTACT", "ops@example.org")
    seeds = root / "seeds.txt"
    seeds.write_text("".join(f"{host}\n" for host in HOSTS), encoding="utf-8")
    original_build_settings = crawl_settings.build_settings

    def fixture_build_settings(cfg: Any, *, tuning: Any = None, extensions: Any = None):
        values = original_build_settings(cfg, tuning=tuning, extensions=extensions)
        handler = "tests.fixtures.latency_download_handler.LatencyDownloadHandler"
        values["DOWNLOAD_HANDLERS"] = {"http": handler, "https": handler}

        async def resolver(hostname: str, port: int = 443, timeout_seconds: float = 5.0) -> None:
            return None

        values["HUST_PUBLIC_RESOLVER"] = resolver
        values["LOG_LEVEL"] = "WARNING"
        return values

    crawl_settings.build_settings = cast(Any, fixture_build_settings)
    runner.build_settings = cast(Any, fixture_build_settings)
    return cli_main(["crawl", "run", "--input", str(seeds), "--output", str(root / "crawl")])


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
