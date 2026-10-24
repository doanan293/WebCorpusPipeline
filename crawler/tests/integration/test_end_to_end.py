from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aiohttp.test_utils import TestServer

from crawler.api import downloader, mediator, merger, reader, storage
from crawler.application.reader.parse import DocumentReaders
from crawler.domain.downloader.models import DownloaderConfig
from crawler.domain.mediator.models import MediatorConfig
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import parse_seeds
from crawler.infra.downloader.http_getter import AiohttpPageGetter
from crawler.infra.downloader.reader_client import HttpReaderClient
from crawler.infra.mediator.http_downloader import HttpDownloader
from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue
from crawler.infra.merger.outbox import JsonlOutbox
from crawler.infra.merger.service_client import HttpServiceClient
from crawler.infra.merger.sqlite_state import SqliteMergerState
from crawler.infra.reader.merger_client import HttpMergerClient
from crawler.infra.reader.readers.dispatch import LocalReaderAdapter
from tests.fakes.downloader import FakeRenderer
from tests.fakes.reader import MemoryParseCache
from tests.fakes.storage import FakeCorpusStore
from tests.integration.services.site import (
    EXPECTED_EDGE,
    EXPECTED_HTML_PATHS,
    EXPECTED_OTHER_PATHS,
    site_app,
)


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
    ports = {name: _free_port() for name in ("mediator", "f1", "f2", "reader", "merger", "storage")}
    url = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
    async with AsyncExitStack() as stack:
        site = await stack.enter_async_context(TestServer(site_app(), host="127.0.0.1"))
        site_port = site.port or 0
        site_url = f"http://127.0.0.1:{site_port}"
        queue = SqliteUrlQueue(tmp_path / "mediator.sqlite3")
        agg_client = HttpMergerClient(url["merger"])
        getters = [
            AiohttpPageGetter(DownloaderConfig(allow_private_addresses=True)) for _ in range(2)
        ]
        reader_clients = [HttpReaderClient(url["reader"]) for _ in range(2)]
        service_client = HttpServiceClient(url["storage"], url["mediator"], backoff=())
        apps = {
            "storage": storage.create_service(store, lambda: "now"),
            "reader": reader.create_service(
                DocumentReaders(
                    LocalReaderAdapter(), None, MemoryParseCache(), tmp_path / "scratch"
                ),
                agg_client,
                closers=(agg_client.close,),
            ),
            "f1": downloader.create_service(
                getters[0],
                FakeRenderer(),
                reader_clients[0],
                closers=(getters[0].close, reader_clients[0].close),
            ),
            "f2": downloader.create_service(
                getters[1],
                FakeRenderer(),
                reader_clients[1],
                closers=(getters[1].close, reader_clients[1].close),
            ),
            "merger": merger.create_service(
                MergerConfig(
                    database=tmp_path / "agg.sqlite3",
                    outbox=tmp_path / "outbox.jsonl",
                    batch_seconds=0.05,
                    outbox_retry_seconds=outbox_retry,
                ),
                SqliteMergerState(tmp_path / "agg.sqlite3"),
                JsonlOutbox(tmp_path / "outbox.jsonl"),
                service_client,
                seeds=parse_seeds([f"{site_url}/"], frozenset({site_port})),
                allowed_ports=frozenset({site_port}),
                closers=(service_client.close,),
            ),
            "mediator": mediator.create_service(
                queue,
                HttpDownloader(ping_timeout=2.0, load_timeout=5.0),
                _Clock(),
                MediatorConfig(
                    downloaders=(url["f1"], url["f2"]),
                    database=tmp_path / "mediator.sqlite3",
                    tick_seconds=0.05,
                    host_delay_seconds=0.05,
                ),
            ),
        }
        # Downstream first, as compose's depends_on does: the mediator pings live downloaders and
        # the merger's seeds reach a live mediator.
        for name in ("storage", "reader", "f1", "f2", "mediator", "merger"):
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
    expected_html = len(EXPECTED_HTML_PATHS)
    expected_other = len(EXPECTED_OTHER_PATHS)

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
    assert set(html) == {f"{site}{path}" for path in EXPECTED_HTML_PATHS}
    assert set(store.collections["other"]) == {f"{site}{path}" for path in EXPECTED_OTHER_PATHS}
    duplicates = [doc for doc in html.values() if doc.get("duplicate_of")]
    assert len(duplicates) == 1
    assert duplicates[0]["content"] == ""
    assert duplicates[0]["duplicate_of"] in {f"{site}/a", f"{site}/b"}
    other = store.collections["other"]
    assert other[f"{site}/doc.pdf"]["format"] == "pdf"
    assert "Thong bao tuyen sinh PDF" in other[f"{site}/doc.pdf"]["content"]
    assert "Bao cao nam hoc DOCX" in other[f"{site}/report.docx"]["content"]
    src, dest, text = EXPECTED_EDGE
    assert {"src": f"{site}{src}", "dest": f"{site}{dest}", "intro": text} in store.graph
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
