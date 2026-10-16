from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from scrapy.crawler import Crawler, CrawlerProcess
from scrapy.settings import Settings

from data_pipeline.crawl.application.common import exact_only_hosts, record_invalid_seeds
from data_pipeline.crawl.application.refresh import (
    RefreshSession,
    RefreshSettings,
    complete_refresh_run,
    preflight,
    prepare_refresh_run,
)
from data_pipeline.crawl.application.requeue import (
    requeue_access_gates,
    requeue_retryable_failures,
)
from data_pipeline.crawl.application.reuse import (
    import_reused_content,
    prepare_reused_asset_targets,
    reused_asset_targets,
    validate_reuse_source,
)
from data_pipeline.crawl.application.writer import CrawlWriter
from data_pipeline.crawl.domain.config import (
    CrawlerConfig,
    runtime_config_snapshot,
    semantic_config_snapshot,
)
from data_pipeline.crawl.domain.exit_codes import TRUNCATION_REASONS, finish_reason_to_exit
from data_pipeline.crawl.domain.frontier.assets import AssetPolicy
from data_pipeline.crawl.domain.frontier.frontier import FrontierPolicy
from data_pipeline.crawl.domain.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.crawl.domain.options import CrawlOptions
from data_pipeline.crawl.domain.seeds import SeedSet
from data_pipeline.crawl.domain.tuning import RuntimeTuning
from data_pipeline.crawl.infrastructure.scrapy.progress import CrawlProgressReporter
from data_pipeline.crawl.infrastructure.scrapy.reuse_spider import (
    ReusedAssetSpider,
    ReusedAssetTarget,
)
from data_pipeline.crawl.infrastructure.scrapy.settings import build_settings
from data_pipeline.crawl.infrastructure.scrapy.unified_spider import UnifiedSpider
from data_pipeline.crawl.infrastructure.sqlite.discovery_rows import (
    host_completion as completion_by_host,
)
from data_pipeline.crawl.infrastructure.sqlite.discovery_rows import (
    iter_host_discovery,
    iter_route_families,
    upsert_host_discovery,
)
from data_pipeline.crawl.infrastructure.sqlite.export import reason_counts
from data_pipeline.crawl.infrastructure.sqlite.html_store import HtmlStore
from data_pipeline.crawl.infrastructure.sqlite.refresh_store import RefreshStore
from data_pipeline.crawl.infrastructure.sqlite.state_store import CrawlState


def _clean_failed_reuse_output(output: Path, state_dir: Path, *, output_existed: bool) -> None:
    if output_existed:
        shutil.rmtree(state_dir, ignore_errors=True)
    else:
        shutil.rmtree(output, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class _CrawlContext:
    """Everything a crawl run is configured with, passed between the run's stages."""

    input_path: Path
    output: Path
    config: CrawlerConfig
    seeds: SeedSet
    options: CrawlOptions
    tuning: RuntimeTuning
    resume: bool
    retry_failed: bool
    retry_access_gates: bool
    reuse_content_from: Path | None
    refresh: bool
    output_existed: bool

    @property
    def state_dir(self) -> Path:
        return self.output / "state"

    @property
    def resuming(self) -> bool:
        return self.resume or self.refresh

    def discard_failed_reuse_output(self) -> None:
        if self.reuse_content_from is not None:
            _clean_failed_reuse_output(
                self.output, self.state_dir, output_existed=self.output_existed
            )


@dataclass(slots=True)
class _Prepared:
    writer: CrawlWriter
    policy: AssetPolicy
    reused_targets: Iterable[ReusedAssetTarget]
    refresh_session: RefreshSession | None
    refresh_targets: list[tuple[str, str]]


def validate_mode_flags(
    *,
    resume: bool,
    retry_failed: bool,
    retry_access_gates: bool,
    reuse_content_from: Path | None,
    refresh: bool,
) -> None:
    """Reject crawl mode flag combinations that have no meaning together."""
    if refresh and (resume or reuse_content_from is not None or retry_failed or retry_access_gates):
        msg = "--refresh cannot be combined with --resume, --reuse-content-from or --retry-* flags"
        raise ValueError(msg)
    for enabled, flag in (
        (retry_failed, "--retry-failed"),
        (retry_access_gates, "--retry-access-gates"),
    ):
        if enabled and not resume:
            msg = f"{flag} requires --resume"
            raise ValueError(msg)


def _validate_modes(ctx: _CrawlContext) -> None:
    validate_mode_flags(
        resume=ctx.resume,
        retry_failed=ctx.retry_failed,
        retry_access_gates=ctx.retry_access_gates,
        reuse_content_from=ctx.reuse_content_from,
        refresh=ctx.refresh,
    )
    source = ctx.reuse_content_from
    if source is None:
        return
    if ctx.resume:
        msg = "--reuse-content-from cannot be combined with --resume"
        raise ValueError(msg)
    if source.resolve() == ctx.output.resolve():
        msg = "--reuse-content-from must differ from --output"
        raise ValueError(msg)
    if ctx.output.exists():
        msg = f"reuse output already exists: {ctx.output}"
        raise FileExistsError(msg)
    validate_reuse_source(source, ctx.input_path)


def _check_resumable(ctx: _CrawlContext) -> str | None:
    """Validate the saved state for --resume/--refresh; return a refresh run to continue."""
    return preflight(ctx.state_dir) if ctx.refresh else None


def _open_state(ctx: _CrawlContext) -> CrawlState:
    return CrawlState.open(
        ctx.state_dir,
        phase="crawl",
        input_path=ctx.input_path,
        semantic_config={
            **semantic_config_snapshot(ctx.config),
            **ctx.options.semantic_snapshot(ctx.seeds),
        },
        runtime_config=runtime_config_snapshot(ctx.config, ctx.tuning),
        resume=ctx.resuming,
    )


def _start_refresh(
    ctx: _CrawlContext, state: CrawlState, continue_run_id: str | None
) -> tuple[RefreshSession, list[tuple[str, str]]]:
    refresh_settings = RefreshSettings.from_config(ctx.config)
    refresh_store = RefreshStore(state.connection)
    run_id, targets = prepare_refresh_run(
        state,
        refresh_store,
        ctx.output,
        ctx.seeds.recursive_hostnames,
        refresh_settings,
        continue_run_id,
        now=time.time(),
    )
    return RefreshSession(refresh_store, run_id, refresh_settings), targets


def _prepare(ctx: _CrawlContext, state: CrawlState, continue_run_id: str | None) -> _Prepared:
    try:
        if ctx.reuse_content_from is not None:
            import_reused_content(ctx.reuse_content_from, state, ctx.input_path)
            prepare_reused_asset_targets(state)
        prepared = _Prepared(
            writer=CrawlWriter(
                ctx.output,
                state,
                ctx.options,
                html_store=(
                    HtmlStore(ctx.output / "html", state.connection)
                    if ctx.config.store_html
                    else None
                ),
            ),
            policy=AssetPolicy(ctx.options),
            reused_targets=reused_asset_targets(state) if ctx.reuse_content_from else (),
            refresh_session=None,
            refresh_targets=[],
        )
        if ctx.refresh:
            prepared.refresh_session, prepared.refresh_targets = _start_refresh(
                ctx, state, continue_run_id
            )
    except Exception:
        state.close()
        ctx.discard_failed_reuse_output()
        raise
    return prepared


def _requeue_for_resume(ctx: _CrawlContext, state: CrawlState) -> None:
    if ctx.retry_failed:
        requeue_retryable_failures(state)
    if ctx.retry_access_gates:
        requeue_access_gates(state)


def _scrapy_crawler(ctx: _CrawlContext) -> tuple[CrawlerProcess, Crawler]:
    extensions: dict[object, int] = {}
    if ctx.tuning.progress_interval_seconds > 0:
        extensions[CrawlProgressReporter] = 500
    settings_map = build_settings(ctx.config, tuning=ctx.tuning, extensions=extensions)
    settings = Settings()
    for key, value in settings_map.items():
        settings.set(key, value, priority="cmdline")
    process = CrawlerProcess(settings=settings)
    spider_cls = ReusedAssetSpider if ctx.reuse_content_from is not None else UnifiedSpider
    return process, process.create_crawler(spider_cls)


class _CrawlRun:
    """One prepared crawl: launches Scrapy and records how the run ended."""

    def __init__(
        self,
        ctx: _CrawlContext,
        state: CrawlState,
        prepared: _Prepared,
    ) -> None:
        self.ctx = ctx
        self.state = state
        self.prepared = prepared
        self.coordinator = SitemapDiscoveryCoordinator(
            exact_hostnames=exact_only_hosts(ctx.seeds),
            on_change=partial(upsert_host_discovery, state),
            strategy="hybrid-unified",
        )
        self.frontier = FrontierPolicy(
            ctx.seeds, ctx.options, route_families=() if ctx.refresh else iter_route_families(state)
        )
        self.resume_records: tuple[dict[str, object], ...] = ()
        if ctx.resuming:
            _requeue_for_resume(ctx, state)
            self.frontier.restore(state.iter_url_records())
            self.coordinator.restore(iter_host_discovery(state))
            self.resume_records = tuple(state.iter_pending_scheduled_records())
        record_invalid_seeds(ctx.seeds, state)
        self.start_time = time.time()
        self.process, self.crawler = _scrapy_crawler(ctx)

    def execute(self) -> int:
        try:
            exit_code = self._run_and_finish()
        except KeyboardInterrupt:
            exit_code = self._record_abort("interrupted", 130)
        except Exception as exc:  # noqa: BLE001 -- any crash is recorded as a failed run before exiting
            exit_code = self._record_abort("failed", 1, error=str(exc))
        finally:
            self.prepared.writer.close()
        return exit_code

    def _launch(self) -> None:
        ctx, prepared = self.ctx, self.prepared
        if ctx.reuse_content_from is not None:
            self.process.crawl(
                self.crawler,
                config=ctx.config,
                options=ctx.options,
                writer=prepared.writer,
                policy=prepared.policy,
                targets=prepared.reused_targets,
            )
        else:
            self.process.crawl(
                self.crawler,
                seeds=ctx.seeds,
                config=ctx.config,
                options=ctx.options,
                frontier=self.frontier,
                writer=prepared.writer,
                policy=prepared.policy,
                coordinator=self.coordinator,
                resume_records=self.resume_records,
                refresh=prepared.refresh_session,
                refresh_targets=prepared.refresh_targets,
            )
        self.process.start()

    def _run_and_finish(self) -> int:
        self._launch()
        ctx, state, session = self.ctx, self.state, self.prepared.refresh_session
        stats = self.crawler.stats.get_stats() if self.crawler.stats else {}
        finish_reason = str(stats.get("finish_reason", "finished"))
        queued = max(
            0, int(stats.get("scheduler/enqueued", 0)) - int(stats.get("scheduler/dequeued", 0))
        )

        counts = state.counts()
        failed = counts.get("url_status_failed", 0)
        host_completion = completion_by_host(state, ctx.seeds.recursive_hostnames)
        zero_content_hosts = sorted(
            hostname
            for hostname, summary in host_completion.items()
            if summary["status"] == "zero_content"
        )
        truncation_counts = reason_counts(state, TRUNCATION_REASONS)
        truncated = sum(truncation_counts.values())
        if session is not None:
            failed, truncated = session.store.failure_counts(session.run_id, TRUNCATION_REASONS)

        elapsed_seconds = time.time() - self.start_time
        avg_rate = int(stats.get("response_received_count", 0)) / max(0.001, elapsed_seconds)
        reporter = getattr(self.crawler, "progress_reporter", None)
        rep_metrics = reporter.metrics() if reporter is not None else {}

        status, exit_code = finish_reason_to_exit(
            finish_reason, queued=queued, failed=failed, truncated=truncated
        )
        self.prepared.writer.publish()
        refresh_summary = (
            complete_refresh_run(state, session, ctx.output, status)
            if session is not None
            else None
        )
        state.finish(
            status=status,
            exit_code=exit_code,
            counts=counts,
            metrics={
                "refresh": refresh_summary,
                "elapsed_seconds": elapsed_seconds,
                "average_rate": avg_rate,
                "recent_rate": rep_metrics.get("recent_rate", avg_rate),
                "peak_queue": rep_metrics.get("peak_queue", queued),
                "stats": stats,
                "frontier": self.frontier.snapshot(),
                "zero_content_hosts": zero_content_hosts,
            },
            truncation=truncation_counts if truncated > 0 else None,
            discovery=self.coordinator.snapshot(),
            frontier=self.frontier.snapshot(),
            host_completion=host_completion,
            public_output=ctx.output,
        )
        return exit_code

    def _record_abort(self, status: str, exit_code: int, **extra_metrics: object) -> int:
        """Record an interrupted or crashed run in the crawl state and return its exit code."""
        self.prepared.writer.publish()
        session = self.prepared.refresh_session
        if session is not None:
            complete_refresh_run(self.state, session, self.ctx.output, status)
        self.state.finish(
            status,
            exit_code=exit_code,
            counts=self.state.counts(),
            metrics={
                "elapsed_seconds": time.time() - self.start_time,
                **extra_metrics,
                "frontier": self.frontier.snapshot(),
            },
            discovery=self.coordinator.snapshot(),
            frontier=self.frontier.snapshot(),
            public_output=self.ctx.output,
        )
        return exit_code


def run_crawl(  # noqa: PLR0913 -- one keyword per documented crawl mode
    input_path: Path,
    output: Path,
    config: CrawlerConfig,
    seeds: SeedSet,
    options: CrawlOptions,
    tuning: RuntimeTuning,
    *,
    resume: bool = False,
    retry_failed: bool = False,
    retry_access_gates: bool = False,
    reuse_content_from: Path | None = None,
    refresh: bool = False,
) -> int:
    ctx = _CrawlContext(
        input_path=input_path,
        output=output,
        config=config,
        seeds=seeds,
        options=options,
        tuning=tuning,
        resume=resume,
        retry_failed=retry_failed,
        retry_access_gates=retry_access_gates,
        reuse_content_from=reuse_content_from,
        refresh=refresh,
        output_existed=output.exists(),
    )
    _validate_modes(ctx)
    continue_run_id = _check_resumable(ctx)
    state = _open_state(ctx)
    prepared = _prepare(ctx, state, continue_run_id)
    try:
        crawl = _CrawlRun(ctx, state, prepared)
    except Exception:
        prepared.writer.close()
        ctx.discard_failed_reuse_output()
        raise
    return crawl.execute()
