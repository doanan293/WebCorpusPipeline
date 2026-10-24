from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from aiohttp.test_utils import TestServer

from crawler.application.reader.parse import DocumentReaders
from crawler.cli.pipeline.crawl import CrawlParts, CrawlSummary, crawl
from crawler.domain.downloader.models import DownloaderConfig
from crawler.domain.mediator.models import CrawlConfig, MediatorConfig
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import parse_seeds
from crawler.infra.downloader.http_getter import AiohttpPageGetter
from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue
from crawler.infra.merger.outbox import JsonlOutbox
from crawler.infra.merger.sqlite_state import SqliteMergerState
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


class _Clock:
    def now(self) -> float:
        return time.time()


def _config(workers: int = 4) -> CrawlConfig:
    mediator = MediatorConfig(
        downloaders=tuple(f"local:{i}" for i in range(workers)),
        database=Path("unused"),
        host_delay_seconds=0.0,
        per_host_concurrency=2,
        autothrottle=False,
        tick_seconds=0.02,
    )
    return CrawlConfig(workers=workers, mediator=mediator)


def _parts(
    tmp_path: Path, store: FakeCorpusStore
) -> tuple[CrawlParts, AiohttpPageGetter, SqliteUrlQueue]:
    getter = AiohttpPageGetter(DownloaderConfig(allow_private_addresses=True))
    queue = SqliteUrlQueue(tmp_path / "mediator.sqlite3")
    parts = CrawlParts(
        queue=queue,
        state=SqliteMergerState(tmp_path / "merger.sqlite3"),
        outbox=JsonlOutbox(tmp_path / "outbox.jsonl"),
        store=store,
        getter=getter,
        renderer=FakeRenderer(),
        readers=DocumentReaders(LocalReaderAdapter(), None, MemoryParseCache(), tmp_path / "s"),
        clock=_Clock(),
        now_iso=lambda: "now",
        merger=MergerConfig(
            database=tmp_path / "merger.sqlite3", batch_seconds=0.02, outbox_retry_seconds=0.2
        ),
    )
    return parts, getter, queue


@asynccontextmanager
async def _site(hits: Counter[str] | None = None) -> AsyncIterator[tuple[str, int]]:
    async with TestServer(site_app(hits), host="127.0.0.1") as server:
        port = server.port or 0
        yield f"http://127.0.0.1:{port}", port


async def _crawl(
    tmp_path: Path, store: FakeCorpusStore, site: str, port: int, seed: str = "/"
) -> CrawlSummary:
    parts, getter, _ = _parts(tmp_path, store)
    try:
        return await asyncio.wait_for(
            crawl(
                _config(),
                parts,
                seeds=parse_seeds([f"{site}{seed}"], frozenset({port})),
                allowed_ports=frozenset({port}),
                forever=False,
                stop=asyncio.Event(),
            ),
            30,
        )
    finally:
        await getter.close()


def test_crawl_run_stores_the_expected_records(tmp_path: Path) -> None:
    store = FakeCorpusStore()

    async def scenario() -> tuple[str, CrawlSummary]:
        async with _site() as (site, port):
            return site, await _crawl(tmp_path, store, site, port)

    site, summary = asyncio.run(scenario())
    assert set(store.collections["html"]) == {f"{site}{path}" for path in EXPECTED_HTML_PATHS}
    assert set(store.collections["other"]) == {f"{site}{path}" for path in EXPECTED_OTHER_PATHS}
    src, dest, text = EXPECTED_EDGE
    assert {"src": f"{site}{src}", "dest": f"{site}{dest}", "text": text} in store.graph
    duplicates = [doc for doc in store.collections["html"].values() if doc.get("duplicate_of")]
    assert len(duplicates) == 1
    assert summary.stored == len(EXPECTED_HTML_PATHS) + len(EXPECTED_OTHER_PATHS)
    assert summary.queued_due == 0


def test_second_run_does_not_refetch(tmp_path: Path) -> None:
    store = FakeCorpusStore()
    hits: Counter[str] = Counter()

    async def scenario() -> None:
        async with _site(hits) as (site, port):
            await _crawl(tmp_path, store, site, port)
            first = sum(hits.values())
            hits.clear()
            await _crawl(tmp_path, store, site, port)
            assert first > 0

    asyncio.run(scenario())
    assert sum(hits.values()) == 0


def test_store_down_records_wait_in_outbox(tmp_path: Path) -> None:
    store = FakeCorpusStore()
    store.down = True

    async def scenario() -> None:
        async with _site() as (site, port):
            await _crawl(tmp_path, store, site, port)

    asyncio.run(scenario())
    assert store.collections["html"] == {}
    pending = list(JsonlOutbox(tmp_path / "outbox.jsonl").pending())
    assert {entry.target for entry in pending} == {"storage"}
    assert len(pending) == len(EXPECTED_HTML_PATHS) + len(EXPECTED_OTHER_PATHS)


def test_interrupt_requeues_in_flight_urls(tmp_path: Path) -> None:
    store = FakeCorpusStore()

    async def scenario() -> str:
        async with _site() as (site, port):
            parts, getter, queue = _parts(tmp_path, store)
            stop = asyncio.Event()
            job = asyncio.create_task(
                crawl(
                    _config(),
                    parts,
                    seeds=parse_seeds([f"{site}/slow"], frozenset({port})),
                    allowed_ports=frozenset({port}),
                    forever=False,
                    stop=stop,
                )
            )
            for _ in range(100):
                row = queue.db.execute("SELECT last_sent_at FROM urls").fetchone()
                if row and row[0]:
                    break
                await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(job, 10)
            await getter.close()
            return f"{site}/slow"

    url = asyncio.run(scenario())
    queue = SqliteUrlQueue(tmp_path / "mediator.sqlite3")
    (due,) = queue.db.execute("SELECT next_due_at FROM urls WHERE url = ?", (url,)).fetchone()
    assert due <= time.time()


def test_crawl_does_not_wait_out_whole_ticks(tmp_path: Path) -> None:
    """A node finishing or a host's wait ending wakes the loop; the tick is only an upper bound."""
    store = FakeCorpusStore()
    slow_tick = replace(_config(), mediator=replace(_config().mediator, tick_seconds=1.0))

    async def scenario() -> float:
        async with _site() as (site, port):
            parts, getter, _ = _parts(tmp_path, store)
            start = time.monotonic()
            try:
                await crawl(
                    slow_tick,
                    parts,
                    seeds=parse_seeds([f"{site}/"], frozenset({port})),
                    allowed_ports=frozenset({port}),
                    forever=False,
                    stop=asyncio.Event(),
                )
            finally:
                await getter.close()
            return time.monotonic() - start

    seconds = asyncio.run(scenario())
    assert len(store.collections["html"]) == len(EXPECTED_HTML_PATHS)
    assert seconds < 2.0
