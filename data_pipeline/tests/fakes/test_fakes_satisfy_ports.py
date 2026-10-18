from __future__ import annotations

from typing import Any

from data_pipeline.application.aggregator import ports as aggregator_ports
from data_pipeline.application.controller import ports as controller_ports
from data_pipeline.application.corpus import ports as corpus_ports
from data_pipeline.application.crawl import ports as crawl_ports
from data_pipeline.application.crawl.asset_responses import AssetResponses
from data_pipeline.application.crawl.discovery_pipeline import DiscoveryPipeline
from data_pipeline.application.crawl.page_pipeline import PagePipeline
from data_pipeline.application.enrich import ports
from data_pipeline.application.fetcher import ports as fetcher_ports
from data_pipeline.application.hosts import ports as hosts_ports
from data_pipeline.application.parser import ports as parser_ports
from data_pipeline.application.storage import ports as storage_ports
from tests.fakes.aggregator import FakeServiceClient, MemoryAggregatorState, MemoryOutbox
from tests.fakes.controller import FakeClock, FakeDownloader, FakeUrlQueue
from tests.fakes.corpus import FakeCorpusDatabase, FakePublisher
from tests.fakes.crawl import (
    FakeCrawlUnitOfWork,
    FakeEngine,
)
from tests.fakes.enrich import (
    FakeArticleSource,
    FakeCloudParser,
    FakeEnrichUnitOfWork,
    FakeLocalParser,
    FakeTransport,
)
from tests.fakes.fetcher import FakeGetter, FakeParserClient, FakeRenderer
from tests.fakes.hosts import FakeBrowser, FakeProber, MemoryReports
from tests.fakes.parser import FakeAggregatorClient, FakeCloud, MemoryParseCache
from tests.fakes.storage import FakeCorpusStore


def _never(*_: object) -> Any:
    raise AssertionError


def _check(instance: object, port: type) -> None:
    attrs = port.__protocol_attrs__  # type: ignore[attr-defined]
    missing = [name for name in attrs if not hasattr(instance, name)]
    assert missing == [], f"{type(instance).__name__} lacks {missing}"


def test_enrich_fakes_satisfy_their_ports() -> None:
    # The annotated assignments are checked by pyrefly; _check catches a missing member at runtime.
    source: ports.ArticleSource = FakeArticleSource([], "c1")
    local: ports.LocalParser = FakeLocalParser(_never)
    cloud: ports.CloudParser = FakeCloudParser(_never)
    transport: ports.HttpTransport = FakeTransport()
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
    engine: crawl_ports.CrawlEngine = FakeEngine()
    _check(engine, crawl_ports.CrawlEngine)


def test_crawl_repository_fakes_satisfy_their_ports() -> None:
    uow: crawl_ports.CrawlUnitOfWork = FakeCrawlUnitOfWork()
    urls: crawl_ports.UrlRecords = uow.urls
    articles: crawl_ports.Articles = uow.articles
    owners: crawl_ports.ContentOwners = uow.owners
    assets: crawl_ports.Assets = uow.assets
    families: crawl_ports.RouteFamilies = uow.families
    hosts: crawl_ports.HostDiscovery = uow.hosts
    refresh: crawl_ports.RefreshRuns = uow.refresh
    html: crawl_ports.RawHtml = uow.html
    run: crawl_ports.CrawlRun = uow.run
    for fake, port in (
        (uow, crawl_ports.CrawlUnitOfWork),
        (urls, crawl_ports.UrlRecords),
        (articles, crawl_ports.Articles),
        (owners, crawl_ports.ContentOwners),
        (assets, crawl_ports.Assets),
        (families, crawl_ports.RouteFamilies),
        (hosts, crawl_ports.HostDiscovery),
        (refresh, crawl_ports.RefreshRuns),
        (html, crawl_ports.RawHtml),
        (run, crawl_ports.CrawlRun),
    ):
        _check(fake, port)


def test_fake_repositories_share_one_memory() -> None:
    uow = FakeCrawlUnitOfWork()
    uow.urls.complete_url(
        {"url": "https://a.test/", "status": "extracted"},
        article={"url": "https://a.test/", "text": "t"},
    )
    assert uow.articles.article("https://a.test/") == {"url": "https://a.test/", "text": "t"}
    assert uow.urls.is_finished("https://a.test/")


def test_enrich_repository_fakes_satisfy_their_ports() -> None:
    uow: ports.EnrichUnitOfWork = FakeEnrichUnitOfWork()
    inventory: ports.Inventory = uow.inventory
    downloads: ports.Downloads = uow.downloads
    parses: ports.Parses = uow.parses
    failures: ports.Failures = uow.failures
    for fake, port in (
        (uow, ports.EnrichUnitOfWork),
        (inventory, ports.Inventory),
        (downloads, ports.Downloads),
        (parses, ports.Parses),
        (failures, ports.Failures),
    ):
        _check(fake, port)


def test_pipelines_are_the_spiders_handlers() -> None:
    handler_attrs = set(crawl_ports.CrawlHandler.__protocol_attrs__)  # type: ignore[attr-defined]
    assert {"start", "resume", "handle", "handle_failure"} <= handler_attrs
    assert "state" not in handler_attrs
    asset_attrs = set(crawl_ports.AssetHandler.__protocol_attrs__)  # type: ignore[attr-defined]
    assert {"try_handle", "handle_failure", "reused"} <= asset_attrs
    discovery_attrs = set(crawl_ports.DiscoveryHandler.__protocol_attrs__)  # type: ignore[attr-defined]
    assert {"start", "handle", "handle_failure"} <= discovery_attrs
    instance_attrs = {"options", "stats", "refreshing", "urls"}
    for cls, attrs in (
        (PagePipeline, handler_attrs),
        (AssetResponses, asset_attrs),
        (DiscoveryPipeline, discovery_attrs),
    ):
        assert attrs <= set(dir(cls)) | instance_attrs, cls


def test_controller_fakes_satisfy_their_ports() -> None:
    queue: controller_ports.UrlQueue = FakeUrlQueue()
    downloader: controller_ports.Downloader = FakeDownloader({})
    clock: controller_ports.Clock = FakeClock(0.0)
    _check(queue, controller_ports.UrlQueue)
    _check(downloader, controller_ports.Downloader)
    _check(clock, controller_ports.Clock)


def test_parser_and_fetcher_fakes_satisfy_their_ports() -> None:
    aggregator: parser_ports.AggregatorClient = FakeAggregatorClient()
    cache: parser_ports.ParseCache = MemoryParseCache()
    cloud: parser_ports.CloudParser = FakeCloud(_never)
    getter: fetcher_ports.PageGetter = FakeGetter({})
    renderer: fetcher_ports.Renderer = FakeRenderer()
    client: fetcher_ports.ParserClient = FakeParserClient()
    for instance, port in (
        (aggregator, parser_ports.AggregatorClient),
        (cache, parser_ports.ParseCache),
        (cloud, parser_ports.CloudParser),
        (getter, fetcher_ports.PageGetter),
        (renderer, fetcher_ports.Renderer),
        (client, fetcher_ports.ParserClient),
    ):
        _check(instance, port)


def test_aggregator_fakes_satisfy_their_ports() -> None:
    state = MemoryAggregatorState()
    seen: aggregator_ports.SeenUrls = state
    owners: aggregator_ports.ContentOwners = state
    outbox: aggregator_ports.Outbox = MemoryOutbox()
    client: aggregator_ports.ServiceClient = FakeServiceClient()
    for instance, port in (
        (seen, aggregator_ports.SeenUrls),
        (owners, aggregator_ports.ContentOwners),
        (outbox, aggregator_ports.Outbox),
        (client, aggregator_ports.ServiceClient),
    ):
        _check(instance, port)


def test_storage_fake_satisfies_its_port() -> None:
    store: storage_ports.CorpusStore = FakeCorpusStore()
    _check(store, storage_ports.CorpusStore)
