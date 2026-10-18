"""The blocks' ports, answered by calling the next block directly instead of over HTTP."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from data_pipeline.application.aggregator.ports import SendResult, Target
from data_pipeline.application.aggregator.runtime import AggregatorRuntime
from data_pipeline.application.controller.add_urls import add_urls
from data_pipeline.application.controller.ports import Clock, LoadResult, UrlQueue
from data_pipeline.application.parser.parse import DocumentReaders, parse_payload
from data_pipeline.application.parser.ports import AggregatorClient
from data_pipeline.application.storage.ports import (
    CorpusStore,
    StoreRejectedError,
    StoreUnavailableError,
)
from data_pipeline.application.storage.store import store_record
from data_pipeline.domain.fetcher.models import Fetched
from data_pipeline.domain.shared.protocol import BUSY, IDLE

log = logging.getLogger(__name__)


class LocalNodes:
    """The ``Downloader`` port over in-process workers: each name is a node doing one URL at a time.

    ``work`` is the whole chain for one URL (fetch, parse, aggregate, store), so a node is "đang
    chờ" again only once its URL has been stored.
    """

    def __init__(self, work: Callable[[str], Awaitable[object]], names: Sequence[str]) -> None:
        self.work = work
        self.names = list(names)
        self.running: dict[str, tuple[str, asyncio.Task[object]]] = {}
        self.finished = asyncio.Event()  # set whenever a node is done, so the loop can wake

    async def ping(self, node: str) -> str | None:
        return BUSY if node in self.running else IDLE

    async def load(self, node: str, url: str) -> LoadResult:
        if node in self.running:
            return "busy"
        task = asyncio.create_task(self._run(url))
        self.running[node] = (url, task)
        task.add_done_callback(lambda _: self._done(node))
        return "accepted"

    def _done(self, node: str) -> None:
        self.running.pop(node, None)
        self.finished.set()

    async def _run(self, url: str) -> object:
        try:
            return await self.work(url)
        except Exception:
            log.exception("crawl of %s crashed", url)
            return "crashed"

    async def close(self) -> None:
        await self.stop()

    async def stop(self) -> list[str]:
        """Stop every URL still in progress; return those URLs so they can be queued again."""
        jobs = list(self.running.values())
        for _, task in jobs:
            task.cancel()
        await asyncio.gather(*(task for _, task in jobs), return_exceptions=True)
        return [url for url, _ in jobs]


class DirectParserClient:
    """The fetcher's ``ParserClient``: reads the page here and hands the record on."""

    def __init__(self, readers: DocumentReaders, aggregator: AggregatorClient) -> None:
        self.readers = readers
        self.aggregator = aggregator

    async def send(self, page: Fetched, fmt: str) -> bool:
        outcome = await parse_payload(
            page.final_url,
            fmt,
            page.content_type,
            page.body,
            readers=self.readers,
            aggregator=self.aggregator,
        )
        return outcome != "unsent"


class DirectAggregatorClient:
    """The parser's ``AggregatorClient``: aggregates the record here."""

    def __init__(self, runtime: AggregatorRuntime) -> None:
        self.runtime = runtime

    async def send(self, record: dict[str, Any]) -> bool:
        await self.runtime.handle(record)
        return True


class DirectServiceClient:
    """The aggregator's ``ServiceClient``: stores records and queues URLs here."""

    def __init__(
        self, store: CorpusStore, queue: UrlQueue, clock: Clock, now_iso: Callable[[], str]
    ) -> None:
        self.store = store
        self.queue = queue
        self.clock = clock
        self.now_iso = now_iso

    async def send(self, target: Target, payload: dict[str, Any]) -> SendResult:
        if target == "controller":
            add_urls(self.queue, payload["urls"], self.clock)
            return "ok"
        try:
            await asyncio.to_thread(store_record, payload, self.store, self.now_iso())
        except StoreUnavailableError:
            return "retry"
        except StoreRejectedError as exc:
            log.warning("refused %s: %s", payload.get("url"), exc)
            return "dead"
        return "ok"
