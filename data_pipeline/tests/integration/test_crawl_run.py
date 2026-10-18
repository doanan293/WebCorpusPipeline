from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from aiohttp.test_utils import TestServer

from data_pipeline.application.parser.parse import DocumentReaders
from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.aggregator.seeds import parse_seeds
from data_pipeline.domain.controller.models import ControllerConfig, CrawlConfig
from data_pipeline.domain.fetcher.models import FetcherConfig
from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox
from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue
from data_pipeline.infrastructure.fetcher.http_getter import AiohttpPageGetter
from data_pipeline.infrastructure.parser.parsers.dispatch import LocalParserAdapter
from data_pipeline.runner.crawl import CrawlParts, CrawlSummary, crawl
from tests.fakes.fetcher import FakeRenderer
from tests.fakes.parser import MemoryParseCache
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
    controller = ControllerConfig(
        downloaders=tuple(f"local:{i}" for i in range(workers)),
        database=Path("unused"),
        host_delay_seconds=0.0,
        per_host_concurrency=2,
        autothrottle=False,
        tick_seconds=0.02,
    )
    return CrawlConfig(workers=workers, controller=controller)


def _parts(
    tmp_path: Path, store: FakeCorpusStore
) -> tuple[CrawlParts, AiohttpPageGetter, SqliteUrlQueue]:
    getter = AiohttpPageGetter(FetcherConfig(allow_private_addresses=True))
    queue = SqliteUrlQueue(tmp_path / "controller.sqlite3")
    parts = CrawlParts(
        queue=queue,
        state=SqliteAggregatorState(tmp_path / "aggregator.sqlite3"),
        outbox=JsonlOutbox(tmp_path / "outbox.jsonl"),
        store=store,
        getter=getter,
        renderer=FakeRenderer(),
        readers=DocumentReaders(LocalParserAdapter(), None, MemoryParseCache(), tmp_path / "s"),
        clock=_Clock(),
        now_iso=lambda: "now",
        aggregator=AggregatorConfig(
            database=tmp_path / "aggregator.sqlite3", batch_seconds=0.02, outbox_retry_seconds=0.2
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
    pending = JsonlOutbox(tmp_path / "outbox.jsonl").pending()
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
    queue = SqliteUrlQueue(tmp_path / "controller.sqlite3")
    (due,) = queue.db.execute("SELECT next_due_at FROM urls WHERE url = ?", (url,)).fetchone()
    assert due <= time.time()
