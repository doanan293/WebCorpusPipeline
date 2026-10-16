from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from data_pipeline.application.crawl.asset_responses import AssetResponses
from data_pipeline.application.crawl.common import exact_only_hosts, record_invalid_seeds
from data_pipeline.application.crawl.page_pipeline import PagePipeline
from data_pipeline.application.crawl.ports import (
    CrawlEngine,
    CrawlStorage,
    CrawlUnitOfWork,
    EngineResult,
    SpiderSetup,
    UrlRecords,
)
from data_pipeline.application.crawl.refresh import (
    RefreshSession,
    RefreshSettings,
    complete_refresh_run,
    preflight,
    prepare_refresh_run,
)
from data_pipeline.application.crawl.requeue import (
    requeue_access_gates,
    requeue_retryable_failures,
)
from data_pipeline.application.crawl.writer import CrawlWriter
from data_pipeline.domain.crawl.config import (
    CrawlerConfig,
    runtime_config_snapshot,
    semantic_config_snapshot,
)
from data_pipeline.domain.crawl.exit_codes import TRUNCATION_REASONS, finish_reason_to_exit
from data_pipeline.domain.crawl.frontier.assets import AssetPolicy
from data_pipeline.domain.crawl.frontier.frontier import FrontierPolicy
from data_pipeline.domain.crawl.frontier.sitemaps import SitemapDiscoveryCoordinator
from data_pipeline.domain.crawl.models import ReusedAssetTarget
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.seeds import SeedSet
from data_pipeline.domain.crawl.tuning import RuntimeTuning


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


def _validate_modes(ctx: _CrawlContext, storage: CrawlStorage) -> None:
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
    storage.validate_reuse_source(source, ctx.input_path)


def _check_resumable(ctx: _CrawlContext, storage: CrawlStorage) -> str | None:
    """Validate the saved state for --resume/--refresh; return a refresh run to continue."""
    return preflight(ctx.state_dir, storage) if ctx.refresh else None


def _open_state(ctx: _CrawlContext, storage: CrawlStorage) -> CrawlUnitOfWork:
    return storage.open(
        ctx.state_dir,
        phase="crawl",
        input_path=ctx.input_path,
        semantic_config={
            **semantic_config_snapshot(ctx.config),
            **ctx.options.semantic_snapshot(ctx.seeds),
        },
        runtime_config=runtime_config_snapshot(ctx.config, ctx.tuning),
        resume=ctx.resuming,
        html_dir=ctx.output / "html",
    )


def _start_refresh(
    ctx: _CrawlContext, uow: CrawlUnitOfWork, continue_run_id: str | None
) -> tuple[RefreshSession, list[tuple[str, str]]]:
    refresh_settings = RefreshSettings.from_config(ctx.config)
    run_id, targets = prepare_refresh_run(
        uow,
        ctx.output,
        ctx.seeds.recursive_hostnames,
        refresh_settings,
        continue_run_id,
        now=time.time(),
    )
    return RefreshSession(uow.refresh, run_id, refresh_settings), targets


def _prepare(
    ctx: _CrawlContext, uow: CrawlUnitOfWork, continue_run_id: str | None, storage: CrawlStorage
) -> _Prepared:
    try:
        if ctx.reuse_content_from is not None:
            storage.import_reused_content(ctx.reuse_content_from, uow, ctx.input_path)
        prepared = _Prepared(
            writer=CrawlWriter(ctx.output, uow, ctx.options, store_html=ctx.config.store_html),
            policy=AssetPolicy(ctx.options),
            reused_targets=storage.reused_asset_targets(uow) if ctx.reuse_content_from else (),
            refresh_session=None,
            refresh_targets=[],
        )
        if ctx.refresh:
            prepared.refresh_session, prepared.refresh_targets = _start_refresh(
                ctx, uow, continue_run_id
            )
    except Exception:
        uow.close()
        ctx.discard_failed_reuse_output()
        raise
    return prepared


def _requeue_for_resume(ctx: _CrawlContext, urls: UrlRecords) -> None:
    if ctx.retry_failed:
        requeue_retryable_failures(urls)
    if ctx.retry_access_gates:
        requeue_access_gates(urls)


class _CrawlRun:
    """One prepared crawl: launches Scrapy and records how the run ended."""

    def __init__(
        self,
        ctx: _CrawlContext,
        uow: CrawlUnitOfWork,
        prepared: _Prepared,
        engine: CrawlEngine,
    ) -> None:
        self.ctx = ctx
        self.uow = uow
        self.prepared = prepared
        self.coordinator = SitemapDiscoveryCoordinator(
            exact_hostnames=exact_only_hosts(ctx.seeds),
            on_change=uow.hosts.upsert_host_discovery,
            strategy="hybrid-unified",
        )
        self.frontier = FrontierPolicy(
            ctx.seeds,
            ctx.options,
            route_families=() if ctx.refresh else uow.families.route_families(),
        )
        self.resume_records: tuple[dict[str, object], ...] = ()
        if ctx.resuming:
            _requeue_for_resume(ctx, uow.urls)
            self.frontier.restore(uow.urls.iter_url_records())
            self.coordinator.restore(uow.hosts.host_discovery())
            self.resume_records = tuple(uow.urls.iter_pending_scheduled_records())
        record_invalid_seeds(ctx.seeds, uow.urls)
        self.start_time = time.time()
        self.prepared_crawl = engine.prepare(ctx.config, ctx.tuning, self._setup())

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

    def _setup(self) -> SpiderSetup:
        ctx, prepared = self.ctx, self.prepared
        if ctx.reuse_content_from is not None:
            return SpiderSetup(
                "reuse",
                {
                    "config": ctx.config,
                    "assets": AssetResponses(
                        options=ctx.options, writer=prepared.writer, policy=prepared.policy
                    ),
                    "targets": prepared.reused_targets,
                },
            )
        pipeline = PagePipeline(
            options=ctx.options,
            frontier=self.frontier,
            writer=prepared.writer,
            policy=prepared.policy,
            coordinator=self.coordinator,
            refresh=prepared.refresh_session,
        )
        return SpiderSetup(
            "unified",
            {
                "seeds": ctx.seeds,
                "config": ctx.config,
                "pipeline": pipeline,
                "resume_records": self.resume_records,
                "refresh_targets": prepared.refresh_targets,
            },
        )

    def _run_and_finish(self) -> int:
        result: EngineResult = self.prepared_crawl.start()
        ctx, uow, session = self.ctx, self.uow, self.prepared.refresh_session
        stats = result.stats
        finish_reason = str(stats.get("finish_reason", "finished"))
        queued = max(
            0, int(stats.get("scheduler/enqueued", 0)) - int(stats.get("scheduler/dequeued", 0))
        )

        counts = uow.run.counts()
        failed = counts.get("url_status_failed", 0)
        host_completion = uow.hosts.host_completion(ctx.seeds.recursive_hostnames)
        zero_content_hosts = sorted(
            hostname
            for hostname, summary in host_completion.items()
            if summary["status"] == "zero_content"
        )
        truncation_counts = uow.run.reason_counts(TRUNCATION_REASONS)
        truncated = sum(truncation_counts.values())
        if session is not None:
            failed, truncated = session.store.failure_counts(session.run_id, TRUNCATION_REASONS)

        elapsed_seconds = time.time() - self.start_time
        avg_rate = int(stats.get("response_received_count", 0)) / max(0.001, elapsed_seconds)
        rep_metrics = result.progress_metrics

        status, exit_code = finish_reason_to_exit(
            finish_reason, queued=queued, failed=failed, truncated=truncated
        )
        self.prepared.writer.publish()
        refresh_summary = (
            complete_refresh_run(uow.run, uow.articles, session, ctx.output, status)
            if session is not None
            else None
        )
        uow.run.finish(
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
            complete_refresh_run(self.uow.run, self.uow.articles, session, self.ctx.output, status)
        self.uow.run.finish(
            status,
            exit_code=exit_code,
            counts=self.uow.run.counts(),
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
    engine: CrawlEngine,
    storage: CrawlStorage,
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
    _validate_modes(ctx, storage)
    continue_run_id = _check_resumable(ctx, storage)
    uow = _open_state(ctx, storage)
    prepared = _prepare(ctx, uow, continue_run_id, storage)
    try:
        crawl = _CrawlRun(ctx, uow, prepared, engine)
    except Exception:
        prepared.writer.close()
        ctx.discard_failed_reuse_output()
        raise
    return crawl.execute()
