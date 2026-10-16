from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.frontier.assets import AssetRole
from data_pipeline.domain.crawl.models import RawPage, ReusedAssetTarget, RouteFamilyState
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.pages import FailedRequest, FetchedPage, FollowUp, Probe, Scheduled
from data_pipeline.domain.crawl.seeds import SeedSet
from data_pipeline.domain.crawl.tuning import RuntimeTuning


class RawHtml(Protocol):
    """Fetched HTML bodies kept for offline re-extraction."""

    def put(
        self,
        url: str,
        body: bytes,
        *,
        final_url: str,
        status: int,
        content_type: str,
        fetched_at: str,
    ) -> str: ...

    def get(self, url: str) -> RawPage | None: ...


class RefreshRuns(Protocol):
    """Refresh runs, their targets and the changes they found."""

    def start_run(self, run_id: str) -> None: ...

    def resume_run(self, run_id: str) -> None: ...

    def latest_run(self) -> dict[str, object] | None: ...

    def run_started_at(self, run_id: str) -> float: ...

    def finish_run(self, run_id: str, status: str, payload: dict[str, object]) -> None: ...

    def completed_run_ids(self, limit: int) -> list[str]: ...

    def add_targets(self, run_id: str, targets: Iterable[tuple[str, str]]) -> None: ...

    def add_target(self, run_id: str, url: str, reason: str) -> bool: ...

    def pending_targets(self, run_id: str) -> list[tuple[str, str]]: ...

    def finish_target(
        self, run_id: str, url: str, outcome: str, error: str | None = None
    ) -> None: ...

    def record_change(self, run_id: str, change: dict[str, object]) -> None: ...

    def iter_changes(self, run_id: str) -> Iterator[dict[str, object]]: ...

    def target_summary(self, run_id: str) -> dict[str, dict[str, int]]: ...

    def failure_counts(self, run_id: str, truncation_reasons: Iterable[str]) -> tuple[int, int]: ...


class UrlRecords(Protocol):
    """The crawl's URL records: what was scheduled, fetched, skipped or failed."""

    def merge_url(
        self,
        record: Mapping[str, object],
        *,
        completed: bool = False,
        keep_scheduled: bool = False,
    ) -> dict[str, object]: ...

    def complete_url(
        self,
        disposition: Mapping[str, object],
        *,
        article: Mapping[str, object] | None = None,
        file: Mapping[str, object] | None = None,
        error: Mapping[str, object] | None = None,
    ) -> None: ...

    def upsert_url(self, record: Mapping[str, object], *, completed: bool = False) -> None: ...

    def insert_url_if_absent(self, record: Mapping[str, object]) -> None: ...

    def put_error(self, record: Mapping[str, object]) -> None: ...

    def is_finished(self, url: str) -> bool: ...

    def is_complete(self, url: str) -> bool: ...

    def checkpoint(self, url: str) -> None: ...

    def url_record(self, url: str) -> dict[str, object] | None: ...

    def url_status(self, url: str) -> str | None: ...

    def update_url_fields(self, url: str, fields: Mapping[str, object]) -> None: ...

    def mark_absent(self, url: str) -> None: ...

    def iter_url_records(self) -> Iterator[dict[str, object]]: ...

    def iter_pending_scheduled_records(self) -> Iterator[dict[str, object]]: ...

    def parsed_probes(self) -> list[tuple[str, str]]: ...

    def pagination_members(self) -> list[tuple[str, str, str | None]]: ...

    def failed_records(self) -> list[tuple[str, dict[str, object], dict[str, object]]]: ...

    def skipped_records(self) -> list[tuple[str, dict[str, object]]]: ...

    def requeue(self, records: list[tuple[str, dict[str, object]]]) -> int: ...


class Articles(Protocol):
    """Extracted articles, and the views over them refresh and reextract use."""

    def article(self, url: str) -> dict[str, object] | None: ...

    def set_article_payload(self, url: str, article: Mapping[str, object]) -> None: ...

    def reextract_candidates(self) -> list[tuple[str, object]]: ...

    def navigation_pages(self) -> list[str]: ...

    def recent_content_candidates(self) -> list[tuple[str, object, object, object]]: ...

    def iter_final_articles(
        self, urls: Iterable[str] | None = None
    ) -> Iterator[tuple[str, str]]: ...


class ContentOwners(Protocol):
    """Which URL owns each piece of content; the others are duplicates of it."""

    def claim_content(self, content_id: str, url: str) -> str: ...

    def release_content_owner(self, url: str, content_id: str) -> None: ...

    def content_owners(self) -> dict[str, str]: ...

    def replace_content_owners(self, urls: Iterable[str], owners: Mapping[str, str]) -> None: ...


class Assets(Protocol):
    """Downloaded files and the pages that reference them."""

    def file_record(self, url: str) -> dict[str, object] | None: ...

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None: ...

    def asset_referrers(self, url: str) -> tuple[str, ...]: ...


class RouteFamilies(Protocol):
    """Pagination families and what the frontier learned about them."""

    def route_families(self) -> Iterator[RouteFamilyState]: ...

    def put_route_family(self, family: RouteFamilyState) -> None: ...


class HostDiscovery(Protocol):
    """Sitemap discovery per host, and the targets a discovery run found."""

    def host_discovery(self) -> Iterator[dict[str, object]]: ...

    def upsert_host_discovery(self, record: Mapping[str, object]) -> None: ...

    def host_completion(self, hostnames: frozenset[str]) -> dict[str, dict[str, object]]: ...

    def put_target(self, record: Mapping[str, object]) -> None: ...


class CrawlRun(Protocol):
    """One run of the crawl: its manifest, counts, finish record and published outputs."""

    root: Path

    manifest: dict[str, Any]

    refresh_run_id: str | None

    @property
    def crawl_id(self) -> str: ...

    def write_manifest(self, value: dict[str, Any]) -> None: ...

    def backup_to(self, destination: Path) -> None: ...

    def counts(self) -> dict[str, int]: ...

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]: ...

    def finish(  # noqa: PLR0913 -- mirrors the adapter's manifest fields
        self,
        status: str,
        exit_code: int,
        counts: dict[str, int],
        metrics: Mapping[str, object],
        truncation: Mapping[str, object] | None = None,
        *,
        discovery: Mapping[str, object] | None = None,
        frontier: Mapping[str, object] | None = None,
        host_completion: Mapping[str, Mapping[str, object]] | None = None,
        public_output: Path | None = None,
    ) -> None: ...

    def export_crawl(self, output: Path) -> None: ...

    def export_discovery(self, output: Path) -> None: ...

    def export_final(self, output: Path) -> None: ...


class CrawlUnitOfWork(Protocol):
    """One open crawl state: its repositories share one connection and one transaction."""

    @property
    def urls(self) -> UrlRecords: ...

    @property
    def articles(self) -> Articles: ...

    @property
    def owners(self) -> ContentOwners: ...

    @property
    def assets(self) -> Assets: ...

    @property
    def families(self) -> RouteFamilies: ...

    @property
    def hosts(self) -> HostDiscovery: ...

    @property
    def refresh(self) -> RefreshRuns: ...

    @property
    def html(self) -> RawHtml: ...

    @property
    def run(self) -> CrawlRun: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def close(self) -> None: ...


class CrawlStorage(Protocol):
    """Opens crawl states and reads what lies next to them (refresh runs, reusable crawls)."""

    def open(
        self,
        root: Path,
        *,
        phase: Literal["discover", "crawl"],
        input_path: Path,
        semantic_config: Mapping[str, object],
        runtime_config: Mapping[str, object],
        resume: bool = False,
        html_dir: Path | None = None,
    ) -> CrawlUnitOfWork: ...

    def open_existing(self, root: Path, *, html_dir: Path | None = None) -> CrawlUnitOfWork: ...

    def manifest(self, state_dir: Path) -> dict[str, Any] | None: ...

    def latest_refresh_run(self, state_dir: Path) -> dict[str, object] | None: ...

    def validate_reuse_source(self, source_output: Path, input_path: Path) -> None: ...

    def import_reused_content(
        self, source_output: Path, uow: CrawlUnitOfWork, input_path: Path
    ) -> None: ...

    def reused_asset_targets(self, uow: CrawlUnitOfWork) -> Iterator[ReusedAssetTarget]: ...


@dataclass(frozen=True)
class SpiderSetup:
    """Which spider a run needs and the keyword arguments its constructor takes."""

    kind: Literal["unified", "reuse", "discovery"]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class EngineResult:
    stats: dict[str, Any]
    progress_metrics: dict[str, Any]


class PreparedCrawl(Protocol):
    def start(self) -> EngineResult:
        """Run the spider to completion (blocking) and return what it reported."""
        ...


class CrawlEngine(Protocol):
    def prepare(
        self, config: CrawlerConfig, tuning: RuntimeTuning, setup: SpiderSetup
    ) -> PreparedCrawl:
        """Build the crawler; raises before anything is fetched if the engine cannot start."""
        ...


class PagePipelineLike(Protocol):
    """What a spider hands fetched pages and failures to (the application's PagePipeline)."""

    options: CrawlOptions
    stats: Callable[[], Any]

    @property
    def urls(self) -> UrlRecords: ...

    @property
    def refreshing(self) -> bool: ...

    def resume(self, records: Iterable[Mapping[str, object]]) -> Iterator[Scheduled]: ...

    def start(self, seeds: SeedSet) -> Iterator[Scheduled]: ...

    def handle(self, page: FetchedPage) -> Iterator[FollowUp]: ...

    def handle_failure(self, failure: FailedRequest) -> list[FollowUp]: ...


class AssetResponsesLike(Protocol):
    """What the reused-asset spider hands its responses and failures to."""

    def try_handle(self, page: FetchedPage) -> bool: ...

    def handle_failure(self, failure: FailedRequest) -> bool: ...

    def reused(self, targets: Iterable[ReusedAssetTarget]) -> Iterator[ReusedAssetTarget]: ...


class DiscoveryPipelineLike(Protocol):
    """What the discovery spider hands its probe responses and failures to."""

    def start(self) -> Iterator[Probe]: ...

    def handle(self, page: FetchedPage) -> Iterator[Probe]: ...

    def handle_failure(self, failure: FailedRequest) -> list[Probe]: ...
