from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aiohttp.test_utils import TestServer

from data_pipeline.application.parser.parse import DocumentReaders
from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.controller.models import ControllerConfig
from data_pipeline.domain.fetcher.models import FetcherConfig
from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox
from data_pipeline.infrastructure.aggregator.service_client import HttpServiceClient
from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from data_pipeline.infrastructure.controller.http_downloader import HttpDownloader
from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue
from data_pipeline.infrastructure.fetcher.http_getter import AiohttpPageGetter
from data_pipeline.infrastructure.fetcher.parser_client import HttpParserClient
from data_pipeline.infrastructure.parser.aggregator_client import HttpAggregatorClient
from data_pipeline.infrastructure.parser.parsers.dispatch import LocalParserAdapter
from dich_vu.api import aggregator, controller, fetcher, parser, storage
from tests.fakes.fetcher import FakeRenderer
from tests.fakes.parser import MemoryParseCache
from tests.fakes.storage import FakeCorpusStore
from tests.integration.services.site import site_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Clock:
    def now(self) -> float:
        return time.time()


@dataclass
class Stack:
    site: str
    store: FakeCorpusStore
    queue: SqliteUrlQueue


@asynccontextmanager
async def _stack(
    tmp_path: Path, store: FakeCorpusStore, outbox_retry: float = 30.0
) -> AsyncIterator[Stack]:
    ports = {
        name: _free_port() for name in ("controller", "f1", "f2", "parser", "aggregator", "storage")
    }
    url = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
    async with AsyncExitStack() as stack:
        site = await stack.enter_async_context(TestServer(site_app(), host="127.0.0.1"))
        site_port = site.port or 0
        site_url = f"http://127.0.0.1:{site_port}"
        queue = SqliteUrlQueue(tmp_path / "controller.sqlite3")
        agg_client = HttpAggregatorClient(url["aggregator"])
        getters = [AiohttpPageGetter(FetcherConfig(allow_private_addresses=True)) for _ in range(2)]
        parser_clients = [HttpParserClient(url["parser"]) for _ in range(2)]
        service_client = HttpServiceClient(url["storage"], url["controller"], backoff=())
        apps = {
            "storage": storage.create_service(store, lambda: "now"),
            "parser": parser.create_service(
                DocumentReaders(
                    LocalParserAdapter(), None, MemoryParseCache(), tmp_path / "scratch"
                ),
                agg_client,
                closers=(agg_client.close,),
            ),
            "f1": fetcher.create_service(
                getters[0],
                FakeRenderer(),
                parser_clients[0],
                closers=(getters[0].close, parser_clients[0].close),
            ),
            "f2": fetcher.create_service(
                getters[1],
                FakeRenderer(),
                parser_clients[1],
                closers=(getters[1].close, parser_clients[1].close),
            ),
            "aggregator": aggregator.create_service(
                AggregatorConfig(
                    database=tmp_path / "agg.sqlite3",
                    outbox=tmp_path / "outbox.jsonl",
                    batch_seconds=0.05,
                    outbox_retry_seconds=outbox_retry,
                ),
                SqliteAggregatorState(tmp_path / "agg.sqlite3"),
                JsonlOutbox(tmp_path / "outbox.jsonl"),
                service_client,
                seed_urls=[f"{site_url}/"],
                allowed_hosts=frozenset({"127.0.0.1"}),
                allowed_ports=frozenset({site_port}),
                closers=(service_client.close,),
            ),
            "controller": controller.create_service(
                queue,
                HttpDownloader(ping_timeout=2.0, load_timeout=5.0),
                _Clock(),
                ControllerConfig(
                    downloaders=(url["f1"], url["f2"]),
                    database=tmp_path / "controller.sqlite3",
                    tick_seconds=0.05,
                    host_delay_seconds=0.05,
                ),
            ),
        }
        # Downstream first, as compose's depends_on does: the controller pings live fetchers and
        # the aggregator's seeds reach a live controller.
        for name in ("storage", "parser", "f1", "f2", "controller", "aggregator"):
            await stack.enter_async_context(
                TestServer(apps[name], host="127.0.0.1", port=ports[name])
            )
        yield Stack(site_url, store, queue)


async def _wait(check: object, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():  # type: ignore[operator]
            return
        await asyncio.sleep(0.1)


def test_site_flows_through_all_blocks(tmp_path: Path) -> None:
    store = FakeCorpusStore()
    expected_html = 3
    expected_other = 2

    async def scenario() -> Stack:
        async with _stack(tmp_path, store) as stack:
            await _wait(
                lambda: (
                    len(store.collections["html"]) == expected_html
                    and len(store.collections["other"]) == expected_other
                ),
                15,
            )
            return stack

    stack = asyncio.run(scenario())
    site = stack.site
    html = store.collections["html"]
    assert set(html) == {f"{site}/", f"{site}/a", f"{site}/b"}
    duplicates = [doc for doc in html.values() if doc.get("duplicate_of")]
    assert len(duplicates) == 1
    assert duplicates[0]["content"] == ""
    assert duplicates[0]["duplicate_of"] in {f"{site}/a", f"{site}/b"}
    other = store.collections["other"]
    assert other[f"{site}/doc.pdf"]["format"] == "pdf"
    assert "Thong bao tuyen sinh PDF" in other[f"{site}/doc.pdf"]["content"]
    assert "Bao cao nam hoc DOCX" in other[f"{site}/report.docx"]["content"]
    assert {"src": f"{site}/", "dest": f"{site}/a", "text": "Bai A"} in store.graph
    queued = [row[0] for row in stack.queue.db.execute("SELECT url FROM urls")]
    assert not any("google.com" in url for url in queued)


def test_storage_down_then_up_delivers_from_outbox(tmp_path: Path) -> None:
    store = FakeCorpusStore()
    store.down = True

    async def scenario() -> None:
        async with _stack(tmp_path, store, outbox_retry=0.3):
            await asyncio.sleep(2.0)
            store.down = False
            await _wait(lambda: len(store.collections["html"]) == 3, 10)

    asyncio.run(scenario())
    assert len(store.collections["html"]) == 3
