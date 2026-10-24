from __future__ import annotations

import asyncio
from pathlib import Path

from crawler.cli.pipeline.adapters import DirectServiceClient, LocalNodes
from crawler.domain.shared.protocol import BUSY, IDLE
from crawler.domain.shared.validators import Validators
from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue
from tests.fakes.mediator import FakeClock
from tests.fakes.storage import FakeCorpusStore

RECORD: dict[str, object] = {
    "url": "https://h/a",
    "format": "html",
    "title": "A",
    "content": "c",
    "out-links": [],
}


def test_local_nodes_report_busy_when_full() -> None:
    release = asyncio.Event()

    async def work(url: str, _: Validators | None) -> object:
        await release.wait()
        return "forwarded"

    async def scenario() -> None:
        nodes = LocalNodes(work, ["local:0"])
        assert await nodes.ping("local:0") == IDLE
        assert await nodes.load("local:0", "https://h/1") == "accepted"
        assert await nodes.ping("local:0") == BUSY
        assert await nodes.load("local:0", "https://h/2") == "busy"
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert await nodes.ping("local:0") == IDLE
        assert await nodes.stop() == []

    asyncio.run(scenario())


def test_local_nodes_close_returns_cancelled_urls() -> None:
    async def work(url: str, _: Validators | None) -> object:
        await asyncio.sleep(60)
        return "forwarded"

    async def scenario() -> list[str]:
        nodes = LocalNodes(work, ["local:0", "local:1"])
        await nodes.load("local:0", "https://h/1")
        await nodes.load("local:1", "https://h/2")
        return sorted(await nodes.stop())

    assert asyncio.run(scenario()) == ["https://h/1", "https://h/2"]


def test_direct_service_client_maps_store_errors(tmp_path: Path) -> None:
    store = FakeCorpusStore()
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    client = DirectServiceClient(store, queue, FakeClock(0.0), lambda: "now")

    assert asyncio.run(client.send("storage", RECORD)) == "ok"
    store.down = True
    assert asyncio.run(client.send("storage", RECORD)) == "retry"
    store.down, store.reject = False, True
    assert asyncio.run(client.send("storage", RECORD)) == "dead"
    assert asyncio.run(client.send("mediator", {"urls": ["https://h/b"]})) == "ok"
    assert queue.counts(0.0).total == 1
