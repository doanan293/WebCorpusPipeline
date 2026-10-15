from __future__ import annotations

import time
from dataclasses import asdict
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from data_pipeline.crawl.config import CrawlerConfig, semantic_config_snapshot
from data_pipeline.crawl.frontier.seeds import SeedSet
from data_pipeline.crawl.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.crawl.output.discovery_writer import DiscoveryWriter
from data_pipeline.crawl.run.common import record_invalid_seeds
from data_pipeline.crawl.run.exit_codes import finish_reason_to_exit
from data_pipeline.crawl.runtime.profiles import RuntimeTuning
from data_pipeline.crawl.runtime.settings import build_settings
from data_pipeline.crawl.spider.discovery import DiscoverySpider
from data_pipeline.crawl.state.discovery import iter_host_discovery, upsert_host_discovery
from data_pipeline.crawl.state.store import CrawlState


def run_discovery(
    input_path: Path,
    output: Path,
    config: CrawlerConfig,
    seeds: SeedSet,
    tuning: RuntimeTuning,
    *,
    resume: bool = False,
) -> int:
    state_dir = output / "state"
    semantic_cfg = dict(semantic_config_snapshot(config))
    runtime_cfg = asdict(tuning)

    state = CrawlState.open(
        state_dir,
        phase="discover",
        input_path=input_path,
        semantic_config=semantic_cfg,
        runtime_config=runtime_cfg,
        resume=resume,
    )
    writer = DiscoveryWriter(output, state)

    exact_hosts = (
        frozenset(
            (urlsplit(u).hostname or "").lower().rstrip(".")
            for u in seeds.exact_urls
            if urlsplit(u).hostname
        )
        - seeds.recursive_hostnames
    )
    coordinator = SitemapDiscoveryCoordinator(
        exact_hostnames=exact_hosts, on_change=partial(upsert_host_discovery, state)
    )
    if resume:
        coordinator.restore(iter_host_discovery(state))

    record_invalid_seeds(seeds, state)

    start_time = time.time()
    extensions: dict[object, int] = {}

    settings_map = build_settings(config, tuning=tuning, extensions=extensions)
    settings = Settings()

    for k, v in settings_map.items():
        settings.set(k, v, priority="cmdline")

    process = CrawlerProcess(settings=settings)
    crawler = process.create_crawler(DiscoverySpider)

    exit_code = 1
    try:
        process.crawl(
            crawler,
            seeds=seeds,
            config=config,
            coordinator=coordinator,
            writer=writer,
        )
        process.start()

        stats = crawler.stats.get_stats() if crawler.stats else {}
        finish_reason = str(stats.get("finish_reason", "finished"))
        enqueued = int(stats.get("scheduler/enqueued", 0))
        dequeued = int(stats.get("scheduler/dequeued", 0))
        queued = max(0, enqueued - dequeued)

        counts = state.counts()
        failed = counts.get("errors", 0)

        status, exit_code = finish_reason_to_exit(
            finish_reason, queued=queued, failed=failed, truncated=0
        )
        writer.publish()
        elapsed_seconds = time.time() - start_time
        metrics = {
            "elapsed_seconds": elapsed_seconds,
            "stats": stats,
        }
        state.finish(
            status=status,
            exit_code=exit_code,
            counts=counts,
            metrics=metrics,
            discovery=coordinator.snapshot(),
            public_output=output,
        )
    except KeyboardInterrupt:
        writer.publish()
        counts = state.counts()
        state.finish(
            "interrupted",
            exit_code=130,
            counts=counts,
            metrics={},
            discovery=coordinator.snapshot(),
            public_output=output,
        )
        exit_code = 130
    except Exception as exc:  # noqa: BLE001 -- any crash is recorded as a failed run before exiting
        writer.publish()
        counts = state.counts()
        state.finish(
            "failed",
            exit_code=1,
            counts=counts,
            metrics={"error": str(exc)},
            discovery=coordinator.snapshot(),
            public_output=output,
        )
        exit_code = 1
    finally:
        writer.close()
    return exit_code
