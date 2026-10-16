from __future__ import annotations

from typing import Any

from data_pipeline.application.corpus import ports as corpus_ports
from data_pipeline.application.crawl import ports as crawl_ports
from data_pipeline.application.enrich import ports
from data_pipeline.application.hosts import ports as hosts_ports
from tests.fakes.corpus import FakeCorpusDatabase, FakePublisher
from tests.fakes.crawl import FakeCrawlState, FakeEngine, FakeHtmlStore, FakeRefreshStore
from tests.fakes.enrich import (
    FakeArticleSource,
    FakeCloudParser,
    FakeLocalParser,
    FakeTransport,
    FakeWorkState,
)
from tests.fakes.hosts import FakeBrowser, FakeProber, MemoryReports


def _never(*_: object) -> Any:
    raise AssertionError


def _check(instance: object, port: type) -> None:
    attrs = port.__protocol_attrs__  # type: ignore[attr-defined]
    missing = [name for name in attrs if not hasattr(instance, name)]
    assert missing == [], f"{type(instance).__name__} lacks {missing}"


def test_enrich_fakes_satisfy_their_ports() -> None:
    # The annotated assignments are checked by pyrefly; _check catches a missing member at runtime.
    state: ports.WorkState = FakeWorkState()
    source: ports.ArticleSource = FakeArticleSource([], "c1")
    local: ports.LocalParser = FakeLocalParser(_never)
    cloud: ports.CloudParser = FakeCloudParser(_never)
    transport: ports.HttpTransport = FakeTransport()
    _check(state, ports.WorkState)
    _check(source, ports.ArticleSource)
    _check(local, ports.LocalParser)
    _check(cloud, ports.CloudParser)
    _check(transport, ports.HttpTransport)


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


def test_crawl_fakes_satisfy_their_ports() -> None:
    state: crawl_ports.CrawlStateStore = FakeCrawlState()
    refresh: crawl_ports.RefreshStorePort = FakeRefreshStore()
    html: crawl_ports.RawHtmlStore = FakeHtmlStore()
    engine: crawl_ports.CrawlEngine = FakeEngine()
    _check(state, crawl_ports.CrawlStateStore)
    _check(refresh, crawl_ports.RefreshStorePort)
    _check(html, crawl_ports.RawHtmlStore)
    _check(engine, crawl_ports.CrawlEngine)
