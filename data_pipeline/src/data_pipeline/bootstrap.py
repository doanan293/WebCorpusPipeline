"""The composition root: builds the adapters each command hands to its use case.

Adapters are imported inside their function, so a command loads only what it uses (one block's
service does not import Playwright, LibreOffice parsers or pymongo of another block).
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
    )
    from data_pipeline.application.fetcher.ports import Renderer
    from data_pipeline.application.hosts.ports import AuditReports, BrowserProber, HostProber
    from data_pipeline.application.parser.parse import DocumentReaders
    from data_pipeline.application.parser.ports import RawPages
    from data_pipeline.application.storage.ports import CorpusStore
    from data_pipeline.domain.aggregator.config import AggregatorConfig
    from data_pipeline.domain.aggregator.seeds import Seeds
    from data_pipeline.domain.controller.models import ControllerConfig
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


# -- shared state -----------------------------------------------------------------------------


def state_lease(path: Path, role: str) -> AbstractContextManager[None]:
    """Hold the one-writer lease of a shared state file; ``StateLockedError`` names the holder."""
    import os
    import socket

    from data_pipeline.infrastructure.shared.lease import SqliteLease

    owner = f"{role}@{socket.gethostname()}:{os.getpid()}"
    return SqliteLease(path, owner, _SystemClock()).held()


# -- parser keys -----------------------------------------------------------------------------


def api_keys(paths: ProjectPaths) -> list[tuple[str, str]]:
    from data_pipeline.infrastructure.shared.settings import load_api_keys

    return load_api_keys(paths.env_file)


# -- corpus -----------------------------------------------------------------------------------


def corpus_files() -> CorpusFiles:
    from data_pipeline.infrastructure.corpus.files.corpus_files import LocalCorpusFiles

    return LocalCorpusFiles()


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


def seeds(path: Path, allowed_ports: frozenset[int] = frozenset()) -> Seeds:
    """The seeds file: one hostname or URL per line."""
    from data_pipeline.domain.aggregator.seeds import parse_seeds

    return parse_seeds(path.read_text(encoding="utf-8").splitlines(), allowed_ports)


def aggregator_state(path: Path) -> AggregatorState:
    from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState

    return SqliteAggregatorState(path)


def outbox(path: Path) -> Outbox:
    from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox

    return JsonlOutbox(path)


def service_client(storage_url: str, controller_url: str) -> HttpServiceClient:
    from data_pipeline.infrastructure.aggregator.service_client import HttpServiceClient

    return HttpServiceClient(storage_url, controller_url)


def raw_pages(crawl: Path) -> RawPages:
    from data_pipeline.infrastructure.parser.raw_pages import SqliteRawPages

    return SqliteRawPages(crawl)
