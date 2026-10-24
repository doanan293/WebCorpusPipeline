from __future__ import annotations

from typing import Any

from data_pipeline.application.corpus import ports as corpus_ports
from data_pipeline.application.downloader import ports as downloader_ports
from data_pipeline.application.hosts import ports as hosts_ports
from data_pipeline.application.mediator import ports as mediator_ports
from data_pipeline.application.merger import ports as merger_ports
from data_pipeline.application.reader import ports as reader_ports
from data_pipeline.application.storage import ports as storage_ports
from tests.fakes.corpus import FakeCorpusDatabase, FakePublisher
from tests.fakes.downloader import FakeGetter, FakeReaderClient, FakeRenderer
from tests.fakes.hosts import FakeBrowser, FakeProber, MemoryReports
from tests.fakes.mediator import FakeClock, FakeDownloader, FakeUrlQueue
from tests.fakes.merger import FakeServiceClient, MemoryMergerState, MemoryOutbox
from tests.fakes.reader import FakeCloud, FakeMergerClient, MemoryParseCache
from tests.fakes.storage import FakeCorpusStore


def _never(*_: object) -> Any:
    raise AssertionError


def _check(instance: object, port: type) -> None:
    attrs = port.__protocol_attrs__  # type: ignore[attr-defined]
    missing = [name for name in attrs if not hasattr(instance, name)]
    assert missing == [], f"{type(instance).__name__} lacks {missing}"


def test_corpus_fakes_satisfy_their_ports() -> None:
    db: corpus_ports.CorpusDatabase = FakeCorpusDatabase()
    publisher: corpus_ports.DatasetPublisher = FakePublisher()
    _check(db, corpus_ports.CorpusDatabase)
    _check(publisher, corpus_ports.DatasetPublisher)


def test_hosts_fakes_satisfy_their_ports() -> None:
    prober: hosts_ports.HostProber = FakeProber(_never)
    browser: hosts_ports.BrowserProber = FakeBrowser(_never)
    reports: hosts_ports.AuditReports = MemoryReports()
    _check(prober, hosts_ports.HostProber)
    _check(browser, hosts_ports.BrowserProber)
    _check(reports, hosts_ports.AuditReports)


def test_mediator_fakes_satisfy_their_ports() -> None:
    queue: mediator_ports.UrlQueue = FakeUrlQueue()
    downloader: mediator_ports.Downloader = FakeDownloader({})
    clock: mediator_ports.Clock = FakeClock(0.0)
    _check(queue, mediator_ports.UrlQueue)
    _check(downloader, mediator_ports.Downloader)
    _check(clock, mediator_ports.Clock)


def test_reader_and_downloader_fakes_satisfy_their_ports() -> None:
    merger: reader_ports.MergerClient = FakeMergerClient()
    cache: reader_ports.ParseCache = MemoryParseCache()
    cloud: reader_ports.CloudReader = FakeCloud(_never)
    getter: downloader_ports.PageGetter = FakeGetter({})
    renderer: downloader_ports.Renderer = FakeRenderer()
    client: downloader_ports.ReaderClient = FakeReaderClient()
    for instance, port in (
        (merger, reader_ports.MergerClient),
        (cache, reader_ports.ParseCache),
        (cloud, reader_ports.CloudReader),
        (getter, downloader_ports.PageGetter),
        (renderer, downloader_ports.Renderer),
        (client, downloader_ports.ReaderClient),
    ):
        _check(instance, port)


def test_merger_fakes_satisfy_their_ports() -> None:
    state = MemoryMergerState()
    seen: merger_ports.SeenUrls = state
    owners: merger_ports.ContentOwners = state
    outbox: merger_ports.Outbox = MemoryOutbox()
    client: merger_ports.ServiceClient = FakeServiceClient()
    for instance, port in (
        (seen, merger_ports.SeenUrls),
        (owners, merger_ports.ContentOwners),
        (outbox, merger_ports.Outbox),
        (client, merger_ports.ServiceClient),
    ):
        _check(instance, port)


def test_storage_fake_satisfies_its_port() -> None:
    store: storage_ports.CorpusStore = FakeCorpusStore()
    _check(store, storage_ports.CorpusStore)
