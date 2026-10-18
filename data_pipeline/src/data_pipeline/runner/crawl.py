"""The five blocks in one process: the same use cases as the services, called directly."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from data_pipeline.application.aggregator.aggregate import Scope
from data_pipeline.application.aggregator.ports import AggregatorState, Outbox, SendResult, Target
from data_pipeline.application.aggregator.runtime import AggregatorRuntime
from data_pipeline.application.controller.dispatch import Dispatcher
from data_pipeline.application.controller.ports import Clock, UrlQueue
from data_pipeline.application.fetcher.fetch import fetch_and_forward
from data_pipeline.application.fetcher.ports import PageGetter, Renderer
from data_pipeline.application.parser.parse import DocumentReaders
from data_pipeline.application.parser.ports import RawPages
from data_pipeline.application.parser.reextract import reextract
from data_pipeline.application.storage.ports import CorpusStore
from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.aggregator.seeds import Seeds
from data_pipeline.domain.controller.models import CrawlConfig
from data_pipeline.runner.adapters import (
    DirectAggregatorClient,
    DirectParserClient,
    DirectServiceClient,
    LocalNodes,
)

log = logging.getLogger(__name__)

# Rounds with nothing to do before a run that is not --forever ends.
_IDLE_ROUNDS = 2
_PROGRESS_SECONDS = 30.0
_IDLE_CHECK_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class CrawlParts:
    """The adapters a crawl runs on, built by the composition root."""

    queue: UrlQueue
    state: AggregatorState
    outbox: Outbox
    store: CorpusStore
    getter: PageGetter
    renderer: Renderer
    readers: DocumentReaders
    clock: Clock
    now_iso: Callable[[], str]
    aggregator: AggregatorConfig


@dataclass(frozen=True, slots=True)
class CrawlSummary:
    sent: int
    stored: int
    failed: int
    queued_due: int


class _CountingClient:
    """Counts what reaches storage for the summary."""

    def __init__(self, inner: DirectServiceClient, counts: Counter[str]) -> None:
        self.inner = inner
        self.counts = counts

    async def send(self, target: Target, payload: dict[str, object]) -> SendResult:
        result = await self.inner.send(target, payload)
        if target == "storage":
            self.counts[f"storage_{result}"] += 1
        return result


async def crawl(
    config: CrawlConfig,
    parts: CrawlParts,
    *,
    seeds: Seeds | None,
    initial_scope: frozenset[str] = frozenset(),
    allowed_ports: frozenset[int] = frozenset(),
    forever: bool,
    stop: asyncio.Event,
) -> CrawlSummary:
    """Crawl until nothing is due (or ``stop`` is set; with ``forever``, only then).

    URLs still being fetched when ``stop`` is set are queued again, due at once; new URLs still
    waiting to be queued go to the outbox, as the aggregator service does when it stops.
    """
    counts: Counter[str] = Counter()
    client = _CountingClient(
        DirectServiceClient(parts.store, parts.queue, parts.clock, parts.now_iso), counts
    )
    runtime = AggregatorRuntime(
        parts.aggregator,
        parts.state,
        parts.outbox,
        client,
        Scope(frozenset(), allowed_ports),
        clock=time.monotonic,
    )
    parser = DirectParserClient(parts.readers, DirectAggregatorClient(runtime))

    async def work(url: str) -> object:
        outcome = await fetch_and_forward(url, parts.getter, parts.renderer, parser)
        counts[outcome] += 1
        return outcome

    nodes = LocalNodes(work, config.controller.downloaders)
    dispatcher = Dispatcher(parts.queue, nodes, parts.clock, config.controller)
    runtime.adopt_initial_scope(initial_scope)
    if seeds is not None:
        await runtime.add_seeds(seeds)
    await runtime.flush()

    idle = 0
    last_flush = last_progress = time.monotonic()
    try:
        while not stop.is_set():
            nodes.finished.clear()
            sent = await dispatcher.step()
            await runtime.tick()
            now = time.monotonic()
            if now - last_flush >= parts.aggregator.outbox_retry_seconds:
                await runtime.flush()
                last_flush = now
            if now - last_progress >= _PROGRESS_SECONDS:
                _log_progress(counts, parts, dispatcher)
                last_progress = now
            if sent:
                idle = 0
                continue
            busy = bool(dispatcher.in_flight() or nodes.running or runtime.batcher.pending)
            due = parts.queue.has_due(parts.clock.now())
            idle = idle + 1 if not (forever or busy or due) else 0
            if idle >= _IDLE_ROUNDS:
                break
            seconds = _wait_seconds(config, parts, dispatcher, idle=idle > 0)
            if runtime.batcher.pending:
                seconds = min(seconds, parts.aggregator.batch_seconds)
            await _wait(stop, nodes.finished, seconds)
    finally:
        cancelled = await nodes.stop()
        now = parts.clock.now()
        for url in cancelled:
            parts.queue.mark_failed(url, 0, now)
        if cancelled:
            log.info("queued again %d urls that were in progress", len(cancelled))
        await runtime.shutdown()
        await parts.renderer.close()
    return CrawlSummary(
        sent=sum(counts[key] for key in ("forwarded", "unsent", "skipped", "blocked", "failed")),
        stored=counts["storage_ok"],
        failed=counts["failed"],
        queued_due=parts.queue.counts(parts.clock.now()).due,
    )


def _wait_seconds(
    config: CrawlConfig, parts: CrawlParts, dispatcher: Dispatcher, *, idle: bool
) -> float:
    """At most a tick; less when a host's wait ends sooner, or while confirming nothing is left."""
    if idle:
        return min(config.controller.tick_seconds, _IDLE_CHECK_SECONDS)
    seconds = config.controller.tick_seconds
    now = parts.clock.now()
    wake = dispatcher.throttle.next_unblock(now)
    if wake is not None:
        seconds = min(seconds, max(0.0, wake - now))
    return seconds


async def _wait(stop: asyncio.Event, finished: asyncio.Event, seconds: float) -> None:
    """Wait ``seconds``, or less if the run is stopped or a node finishes."""
    waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(finished.wait())]
    try:
        await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


def _log_progress(counts: Counter[str], parts: CrawlParts, dispatcher: Dispatcher) -> None:
    queue = parts.queue.counts(parts.clock.now())
    log.info(
        "crawl: %d fetched, %d stored, %d failed, %d in flight, %d due of %d urls",
        counts["forwarded"],
        counts["storage_ok"],
        counts["failed"],
        dispatcher.in_flight(),
        queue.due,
        queue.total,
    )


async def add_seeds(
    parts: CrawlParts, seeds: Seeds, initial_scope: frozenset[str] = frozenset()
) -> int:
    """Put ``seeds`` into the shared state without crawling; return how many URLs were new."""
    client = DirectServiceClient(parts.store, parts.queue, parts.clock, parts.now_iso)
    runtime = AggregatorRuntime(
        parts.aggregator,
        parts.state,
        parts.outbox,
        client,
        Scope(frozenset()),
        clock=time.monotonic,
    )
    before = parts.queue.counts(parts.clock.now()).total
    runtime.adopt_initial_scope(initial_scope)  # first, or a first seed would narrow the scope
    await runtime.add_seeds(seeds)
    return parts.queue.counts(parts.clock.now()).total - before


async def reextract_here(
    parts: CrawlParts, pages: RawPages, initial_scope: frozenset[str] = frozenset()
) -> int:
    """``reextract`` in one process: records go through the aggregator's work into the store."""
    client = DirectServiceClient(parts.store, parts.queue, parts.clock, parts.now_iso)
    runtime = AggregatorRuntime(
        parts.aggregator,
        parts.state,
        parts.outbox,
        client,
        Scope(frozenset()),
        clock=time.monotonic,
    )
    runtime.adopt_initial_scope(initial_scope)
    sent = await reextract(pages, DirectAggregatorClient(runtime))
    await runtime.shutdown()  # the last new links: into the outbox, then queued just below
    await runtime.flush()
    return sent
