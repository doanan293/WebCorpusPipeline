from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from data_pipeline.application.crawl.common import exact_only_hosts, record_invalid_seeds
from data_pipeline.application.crawl.discovery_pipeline import DiscoveryPipeline
from data_pipeline.application.crawl.discovery_writer import DiscoveryWriter
from data_pipeline.application.crawl.ports import CrawlEngine, CrawlStorage, SpiderSetup
from data_pipeline.domain.crawl.config import CrawlerConfig, semantic_config_snapshot
from data_pipeline.domain.crawl.exit_codes import finish_reason_to_exit
from data_pipeline.domain.crawl.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.domain.crawl.seeds import SeedSet
from data_pipeline.domain.crawl.tuning import RuntimeTuning


def run_discovery(
    input_path: Path,
    output: Path,
    config: CrawlerConfig,
    seeds: SeedSet,
    tuning: RuntimeTuning,
    *,
    resume: bool = False,
    engine: CrawlEngine,
    storage: CrawlStorage,
) -> int:
    state_dir = output / "state"
    semantic_cfg = dict(semantic_config_snapshot(config))
    runtime_cfg = asdict(tuning)

    state = storage.open(
        state_dir,
        phase="discover",
        input_path=input_path,
        semantic_config=semantic_cfg,
        runtime_config=runtime_cfg,
        resume=resume,
    )
    writer = DiscoveryWriter(output, state)

    coordinator = SitemapDiscoveryCoordinator(
        exact_hostnames=exact_only_hosts(seeds), on_change=state.upsert_host_discovery
    )
    if resume:
        coordinator.restore(state.host_discovery())

    record_invalid_seeds(seeds, state)

    start_time = time.time()
    setup = SpiderSetup(
        "discovery",
        {
            "config": config,
            "pipeline": DiscoveryPipeline(seeds=seeds, coordinator=coordinator, writer=writer),
        },
    )
    prepared = engine.prepare(config, tuning, setup)

    exit_code = 1
    try:
        stats = prepared.start().stats
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
