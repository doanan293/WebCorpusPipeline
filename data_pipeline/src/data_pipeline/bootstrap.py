"""The composition root: builds the adapters each command hands to its use case.

Adapters that need an optional extra (Scrapy, Playwright, aiohttp, the parsers) are imported
inside their function, so a command only needs the extras it actually uses.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from data_pipeline.domain.shared.config import ProjectPaths, Settings
from data_pipeline.infrastructure.corpus.mongo import MongoCorpusDatabase, connect
from data_pipeline.infrastructure.shared import env, settings

if TYPE_CHECKING:
    from data_pipeline.application.aggregator.ports import AggregatorState, Outbox
    from data_pipeline.application.controller.ports import Clock, Downloader, UrlQueue
    from data_pipeline.application.corpus.ports import (
        CorpusDatabase,
        CorpusFiles,
        DatasetPublisher,
        EdgeIndexFactory,
        EnrichmentCounts,
        EnrichmentStatus,
        ExportDocuments,
    )
    from data_pipeline.application.crawl.ports import CrawlEngine, CrawlStorage
    from data_pipeline.application.enrich.ports import (
        ArticleSource,
        CloudParser,
        DownloadFiles,
        EnrichUnitOfWork,
        LocalParser,
        ParseCache,
        Parses,
        TransportFactory,
    )
    from data_pipeline.application.fetcher.ports import Renderer
    from data_pipeline.application.hosts.ports import AuditReports, BrowserProber, HostProber
    from data_pipeline.application.parser.parse import DocumentReaders
    from data_pipeline.application.storage.ports import CorpusStore
    from data_pipeline.domain.aggregator.config import AggregatorConfig
    from data_pipeline.domain.controller.models import ControllerConfig
    from data_pipeline.domain.crawl.config import CrawlerConfig
    from data_pipeline.domain.crawl.seeds import SeedSet
    from data_pipeline.domain.fetcher.models import FetcherConfig
    from data_pipeline.domain.parser.config import ParserConfig
    from data_pipeline.infrastructure.aggregator.service_client import HttpServiceClient
    from data_pipeline.infrastructure.fetcher.http_getter import AiohttpPageGetter
    from data_pipeline.infrastructure.fetcher.parser_client import HttpParserClient
    from data_pipeline.infrastructure.parser.aggregator_client import HttpAggregatorClient

# -- configuration ----------------------------------------------------------------------------


def project_paths() -> ProjectPaths:
    """The project's well-known locations (the directory holding ``pyproject.toml``)."""
    return settings.project_paths()


def load_settings(paths: ProjectPaths, overrides: Mapping[str, Any] | None = None) -> Settings:
    """``config/pipeline.yaml`` and the environment, with command-line ``overrides`` on top."""
    return settings.load_settings(paths, overrides)


def load_env(paths: ProjectPaths) -> None:
    """Load the project's ``.env`` into the process environment."""
    env.load_env(paths)


# -- crawl ------------------------------------------------------------------------------------


def crawl_engine() -> CrawlEngine:
    from data_pipeline.infrastructure.crawl.scrapy.runner import ScrapyEngine

    return ScrapyEngine()


def crawl_storage() -> CrawlStorage:
    from data_pipeline.infrastructure.crawl.sqlite.storage import SqliteCrawlStorage

    return SqliteCrawlStorage()


def crawler_config(path: Path, *, hostnames: frozenset[str]) -> CrawlerConfig:
    from data_pipeline.infrastructure.crawl.config_file import load_crawler_config

    return load_crawler_config(path, hostnames=hostnames)


def seed_file(path: Path) -> SeedSet:
    from data_pipeline.infrastructure.crawl.seeds_file import parse_seed_file

    return parse_seed_file(path)


# -- enrich -----------------------------------------------------------------------------------


def article_source(path: Path) -> ArticleSource:
    from data_pipeline.infrastructure.enrich.files.articles import JsonlArticleSource

    return JsonlArticleSource(path)


def inventory_key(path: Path) -> str:
    """The key the work state files the inventory of ``path`` under."""
    from data_pipeline.infrastructure.enrich.files.articles import source_key

    return source_key(path)


def source_crawl_id(settings: Settings) -> str | None:
    """The crawl id recorded next to the crawl output enrichment reads."""
    return article_source(settings.source_path).crawl_id()


def work_state(settings: Settings, *, rebind: bool) -> AbstractContextManager[EnrichUnitOfWork]:
    """Open the work directory bound to the crawl that produced the source."""
    from data_pipeline.application.enrich import session
    from data_pipeline.infrastructure.enrich.sqlite.unit_of_work import SqliteEnrichUnitOfWork

    return session.open_work_state(
        settings,
        rebind=rebind,
        crawl_id=source_crawl_id(settings),
        opener=SqliteEnrichUnitOfWork.open,
    )


def cloud_job_status(work_path: Path, request_id: str, job_id: str) -> str | None:
    from data_pipeline.infrastructure.enrich.sqlite.unit_of_work import read_cloud_job_status

    return read_cloud_job_status(work_path, request_id, job_id)


def download_files() -> DownloadFiles:
    from data_pipeline.infrastructure.enrich.files.download_files import LocalDownloadFiles

    return LocalDownloadFiles()


def http_transport_factory() -> TransportFactory:
    from data_pipeline.infrastructure.enrich.http.aiohttp_transport import PublicTransport

    return PublicTransport


def api_keys(paths: ProjectPaths) -> list[tuple[str, str]]:
    from data_pipeline.infrastructure.shared.settings import load_api_keys

    return load_api_keys(paths.env_file)


def local_parser() -> LocalParser:
    from data_pipeline.infrastructure.parser.parsers.dispatch import LocalParserAdapter

    return LocalParserAdapter()


def cloud_parser(parses: Parses, settings: Settings, keys: list[tuple[str, str]]) -> CloudParser:
    from data_pipeline.infrastructure.parser.llamaparse import LlamaParseCloud

    return LlamaParseCloud(parses, settings, keys)


def parse_cache(root: Path) -> ParseCache:
    from data_pipeline.infrastructure.parser.cache import FileParseCache

    return FileParseCache(root / "cache")


# -- corpus -----------------------------------------------------------------------------------


def corpus_files() -> CorpusFiles:
    from data_pipeline.infrastructure.corpus.files.corpus_files import LocalCorpusFiles

    return LocalCorpusFiles()


def edge_index() -> EdgeIndexFactory:
    from data_pipeline.infrastructure.corpus.graph_index import open_edge_index

    return open_edge_index


def dataset_publisher() -> DatasetPublisher:
    from data_pipeline.infrastructure.corpus.kaggle_cli import KaggleCli

    return KaggleCli()


@contextmanager
def mongo_database(settings: Settings) -> Iterator[CorpusDatabase]:
    """The corpus database in MongoDB; the client is closed however the block ends."""
    client = connect(settings)
    try:
        yield MongoCorpusDatabase(client[settings.mongo_database])
    finally:
        client.close()


def export_documents(uow: EnrichUnitOfWork) -> ExportDocuments:
    """The enrich export, handed to corpus packaging (corpus may not import enrich)."""
    from data_pipeline.application.enrich.export import export_enrichment

    def export(folder: Path) -> Path:
        return export_enrichment(uow.inventory, uow.parses, uow.summary(), folder)

    return export


# -- domains ----------------------------------------------------------------------------------


def host_prober(*, timeout: float, attempts: int) -> HostProber:
    from data_pipeline.infrastructure.hosts.http_probe import HttpHostProber

    return HttpHostProber(timeout=timeout, attempts=attempts)


def browser_prober(*, enabled: bool, timeout: float, workers: int) -> BrowserProber | None:
    """The headless-browser prober, or None when browser probes are off."""
    if not enabled:
        return None
    # Imported only when needed, so --no-browser works without the 'crawl' extra.
    from data_pipeline.infrastructure.hosts.browser_probe import PlaywrightBrowserProber

    return PlaywrightBrowserProber(timeout=timeout, workers=workers)


def audit_reports(all_path: Path, active_path: Path, report_path: Path) -> AuditReports:
    from data_pipeline.infrastructure.hosts.report_files import FileAuditReports

    return FileAuditReports(all_path, active_path, report_path)


class _EnrichmentStatus:
    """The corpus's view of an enrich unit of work (corpus may not import enrich)."""

    def __init__(self, uow: EnrichUnitOfWork) -> None:
        self.uow = uow

    def inventory_done(self, source_key: str) -> bool:
        return self.uow.inventory.inventory_done(source_key)

    def summary(self) -> EnrichmentCounts:
        return self.uow.summary()

    def pending_parse(self) -> Iterator[object]:
        return self.uow.parses.pending_parse()


def enrichment_status(uow: EnrichUnitOfWork) -> EnrichmentStatus:
    """How far enrichment has got, as the corpus packaging asks it."""
    return _EnrichmentStatus(uow)


# -- controller -------------------------------------------------------------------------------


def controller_config(path: Path) -> ControllerConfig:
    from data_pipeline.infrastructure.controller.config_file import load_controller_config

    return load_controller_config(path)


def url_queue(path: Path) -> UrlQueue:
    from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue

    return SqliteUrlQueue(path)


def downloader(config: ControllerConfig) -> Downloader:
    from data_pipeline.infrastructure.controller.http_downloader import HttpDownloader

    return HttpDownloader(
        ping_timeout=config.ping_timeout_seconds, load_timeout=config.load_timeout_seconds
    )


class _SystemClock:
    def now(self) -> float:
        return time.time()


def system_clock() -> Clock:
    return _SystemClock()


# -- fetcher ----------------------------------------------------------------------------------


def fetcher_config(path: Path) -> FetcherConfig:
    from data_pipeline.infrastructure.fetcher.config_file import load_fetcher_config

    return load_fetcher_config(path)


def page_getter(config: FetcherConfig) -> AiohttpPageGetter:
    from data_pipeline.infrastructure.fetcher.http_getter import AiohttpPageGetter

    return AiohttpPageGetter(config)


def renderer(config: FetcherConfig) -> Renderer:
    from data_pipeline.infrastructure.fetcher.browser import PlaywrightRenderer

    return PlaywrightRenderer(config)


def parser_client(parser_url: str) -> HttpParserClient:
    from data_pipeline.infrastructure.fetcher.parser_client import HttpParserClient

    return HttpParserClient(parser_url)


# -- parser -----------------------------------------------------------------------------------


def parser_config(path: Path) -> ParserConfig:
    from data_pipeline.infrastructure.parser.config_file import load_parser_config

    return load_parser_config(path)


def aggregator_client(aggregator_url: str) -> HttpAggregatorClient:
    from data_pipeline.infrastructure.parser.aggregator_client import HttpAggregatorClient

    return HttpAggregatorClient(aggregator_url)


def document_readers(config: ParserConfig, paths: ProjectPaths) -> DocumentReaders:
    """Local parsers, the LlamaParse cache, and LlamaParse itself when enabled and keyed."""
    import logging
    import tempfile

    from data_pipeline.application.parser.parse import DocumentReaders
    from data_pipeline.infrastructure.parser.cache import FileParseCache
    from data_pipeline.infrastructure.parser.job_log import LoggedCloudJobs
    from data_pipeline.infrastructure.parser.llamaparse import LlamaParseCloud
    from data_pipeline.infrastructure.parser.parsers.dispatch import LocalParserAdapter

    cloud = None
    if config.llamaparse:
        keys = api_keys(paths)
        if keys:
            cloud = LlamaParseCloud(
                LoggedCloudJobs(),
                Settings(
                    project_root=paths.root,
                    cloud_poll_seconds=config.cloud_poll_seconds,
                    cloud_timeout_seconds=config.cloud_timeout_seconds,
                ),
                keys,
            )
        else:
            logging.getLogger(__name__).warning("llamaparse is on but .env has no LLAMAPARSE_KEY")
    cache_dir = (
        config.cache_dir if config.cache_dir.is_absolute() else paths.root / config.cache_dir
    )
    scratch = Path(tempfile.gettempdir()) / "data-pipeline-parse"
    return DocumentReaders(LocalParserAdapter(), cloud, FileParseCache(cache_dir), scratch)


# -- storage ----------------------------------------------------------------------------------


def corpus_store(uri: str, database: str) -> CorpusStore:
    from data_pipeline.infrastructure.storage.mongo import MongoCorpusStore
    from data_pipeline.infrastructure.storage.mongo import connect as connect_store

    return MongoCorpusStore(connect_store(uri)[database])


def utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# -- aggregator -------------------------------------------------------------------------------


def aggregator_config(path: Path) -> AggregatorConfig:
    from data_pipeline.infrastructure.aggregator.config_file import load_aggregator_config

    return load_aggregator_config(path)


def hostnames(path: Path) -> frozenset[str]:
    from data_pipeline.infrastructure.shared.files import load_hostnames

    return load_hostnames(path)


def aggregator_state(path: Path) -> AggregatorState:
    from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState

    return SqliteAggregatorState(path)


def outbox(path: Path) -> Outbox:
    from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox

    return JsonlOutbox(path)


def service_client(storage_url: str, controller_url: str) -> HttpServiceClient:
    from data_pipeline.infrastructure.aggregator.service_client import HttpServiceClient

    return HttpServiceClient(storage_url, controller_url)
