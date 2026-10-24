"""The composition root: builds the adapters each command hands to its use case.

Adapters are imported inside their function, so a command loads only what it uses (one block's
service does not import Playwright, LibreOffice readers or pymongo of another block).
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crawler.domain.shared.config import ProjectPaths, Settings
from crawler.infra.corpus.mongo import MongoCorpusDatabase, connect
from crawler.infra.shared import env, settings

if TYPE_CHECKING:
    from crawler.application.corpus.ports import (
        CorpusDatabase,
        CorpusFiles,
        DatasetPublisher,
    )
    from crawler.application.downloader.ports import Renderer
    from crawler.application.hosts.ports import AuditReports, BrowserProber, HostProber
    from crawler.application.mediator.ports import Clock, Downloader, UrlQueue
    from crawler.application.merger.ports import MergerState, Outbox
    from crawler.application.reader.parse import DocumentReaders
    from crawler.application.reader.ports import RawPages
    from crawler.application.storage.ports import CorpusStore, ImageGetter, PictureStore
    from crawler.domain.downloader.models import DownloaderConfig
    from crawler.domain.mediator.models import CrawlConfig, MediatorConfig
    from crawler.domain.merger.config import MergerConfig
    from crawler.domain.merger.seeds import Seeds
    from crawler.domain.reader.config import ReaderConfig
    from crawler.infra.downloader.http_getter import AiohttpPageGetter
    from crawler.infra.downloader.reader_client import HttpReaderClient
    from crawler.infra.merger.service_client import HttpServiceClient
    from crawler.infra.reader.merger_client import HttpMergerClient

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

    from crawler.infra.shared.lease import SqliteLease

    owner = f"{role}@{socket.gethostname()}:{os.getpid()}"
    return SqliteLease(path, owner, _SystemClock()).held()


# -- reader keys -----------------------------------------------------------------------------


def api_keys(paths: ProjectPaths) -> list[tuple[str, str]]:
    from crawler.infra.shared.settings import load_api_keys

    return load_api_keys(paths.env_file)


# -- corpus -----------------------------------------------------------------------------------


def corpus_files() -> CorpusFiles:
    from crawler.infra.corpus.files.corpus_files import LocalCorpusFiles

    return LocalCorpusFiles()


def dataset_publisher() -> DatasetPublisher:
    from crawler.infra.corpus.kaggle_cli import KaggleCli

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
    from crawler.infra.hosts.http_probe import HttpHostProber

    return HttpHostProber(timeout=timeout, attempts=attempts)


def browser_prober(*, enabled: bool, timeout: float, workers: int) -> BrowserProber | None:
    """The headless-browser prober, or None when browser probes are off."""
    if not enabled:
        return None
    # Imported only when needed, so --no-browser works without the 'crawl' extra.
    from crawler.infra.hosts.browser_probe import PlaywrightBrowserProber

    return PlaywrightBrowserProber(timeout=timeout, workers=workers)


def audit_reports(all_path: Path, active_path: Path, report_path: Path) -> AuditReports:
    from crawler.infra.hosts.report_files import FileAuditReports

    return FileAuditReports(all_path, active_path, report_path)


# -- mediator -------------------------------------------------------------------------------


def mediator_config(path: Path) -> MediatorConfig:
    from crawler.infra.mediator.config_file import load_mediator_config

    return load_mediator_config(path)


def crawl_config(crawl_path: Path, mediator_path: Path) -> CrawlConfig:
    from crawler.infra.mediator.config_file import load_crawl_config

    return load_crawl_config(crawl_path, mediator_path)


def url_queue(path: Path) -> UrlQueue:
    from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue

    return SqliteUrlQueue(path)


def downloader(config: MediatorConfig) -> Downloader:
    from crawler.infra.mediator.http_downloader import HttpDownloader

    return HttpDownloader(
        ping_timeout=config.ping_timeout_seconds, load_timeout=config.load_timeout_seconds
    )


class _SystemClock:
    def now(self) -> float:
        return time.time()


def system_clock() -> Clock:
    return _SystemClock()


# -- downloader ----------------------------------------------------------------------------------


def downloader_config(path: Path) -> DownloaderConfig:
    from crawler.infra.downloader.config_file import load_downloader_config

    return load_downloader_config(path)


def page_getter(config: DownloaderConfig) -> AiohttpPageGetter:
    from crawler.infra.downloader.http_getter import AiohttpPageGetter

    return AiohttpPageGetter(config)


def renderer(config: DownloaderConfig) -> Renderer:
    from crawler.infra.downloader.browser import PlaywrightRenderer

    return PlaywrightRenderer(config)


def reader_client(reader_url: str) -> HttpReaderClient:
    from crawler.infra.downloader.reader_client import HttpReaderClient

    return HttpReaderClient(reader_url)


# -- reader -----------------------------------------------------------------------------------


def reader_config(path: Path) -> ReaderConfig:
    from crawler.infra.reader.config_file import load_reader_config

    return load_reader_config(path)


def merger_client(merger_url: str) -> HttpMergerClient:
    from crawler.infra.reader.merger_client import HttpMergerClient

    return HttpMergerClient(merger_url)


def document_readers(config: ReaderConfig, paths: ProjectPaths) -> DocumentReaders:
    """Local readers, the LlamaParse cache, and LlamaParse itself when enabled and keyed."""
    import asyncio
    import logging
    import tempfile

    from crawler.application.reader.parse import DocumentReaders
    from crawler.infra.reader.cache import FileParseCache
    from crawler.infra.reader.job_log import LoggedCloudJobs
    from crawler.infra.reader.llamaparse import LlamaParseCloud
    from crawler.infra.reader.readers.dispatch import LocalReaderAdapter

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
    scratch = Path(tempfile.gettempdir()) / "crawler-parse"
    return DocumentReaders(
        LocalReaderAdapter(),
        cloud,
        FileParseCache(cache_dir),
        scratch,
        asyncio.Semaphore(config.document_workers),
    )


# -- storage ----------------------------------------------------------------------------------


def corpus_store(uri: str, database: str) -> CorpusStore:
    from crawler.infra.storage.mongo import MongoCorpusStore
    from crawler.infra.storage.mongo import connect as connect_store

    return MongoCorpusStore(connect_store(uri)[database])


def picture_store(uri: str, database: str) -> PictureStore:
    from crawler.infra.storage.mongo import MongoPictureStore
    from crawler.infra.storage.mongo import connect as connect_store

    return MongoPictureStore(connect_store(uri)[database])


def image_getter() -> ImageGetter:
    from crawler.infra.storage.image_getter import AiohttpImageGetter

    return AiohttpImageGetter()


def utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# -- merger -------------------------------------------------------------------------------


def merger_config(path: Path) -> MergerConfig:
    from crawler.infra.merger.config_file import load_merger_config

    return load_merger_config(path)


def hostnames(path: Path) -> frozenset[str]:
    from crawler.infra.shared.files import load_hostnames

    return load_hostnames(path)


def seeds(path: Path, allowed_ports: frozenset[int] = frozenset()) -> Seeds:
    """The seeds file: one hostname or URL per line."""
    from crawler.domain.merger.seeds import parse_seeds

    return parse_seeds(path.read_text(encoding="utf-8").splitlines(), allowed_ports)


def merger_state(path: Path) -> MergerState:
    from crawler.infra.merger.sqlite_state import SqliteMergerState

    return SqliteMergerState(path)


def outbox(path: Path) -> Outbox:
    from crawler.infra.merger.outbox import JsonlOutbox

    return JsonlOutbox(path)


def service_client(storage_url: str, mediator_url: str) -> HttpServiceClient:
    from crawler.infra.merger.service_client import HttpServiceClient

    return HttpServiceClient(storage_url, mediator_url)


def raw_pages(crawl: Path) -> RawPages:
    from crawler.infra.reader.raw_pages import SqliteRawPages

    return SqliteRawPages(crawl)
