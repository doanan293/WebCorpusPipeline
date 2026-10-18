"""Tổng hợp dữ liệu (block 4, port 3300): ``POST /add``; feeds storage and the controller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from aiohttp import web

from data_pipeline.application.aggregator.aggregate import Scope
from data_pipeline.application.aggregator.ports import AggregatorState, Outbox, ServiceClient
from data_pipeline.application.aggregator.runtime import AggregatorRuntime
from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.shared.protocol import validate_record
from dich_vu.api.common import Stats, error, json_object, stats_loop

log = logging.getLogger(__name__)

BATCH_TICK_SECONDS = 0.2


def create_app(
    handle: Callable[[dict[str, Any]], Awaitable[None]], stats: Stats | None = None
) -> web.Application:
    counters = stats or Stats()

    async def add(request: web.Request) -> web.Response:
        body = await json_object(request)
        if not body:
            return web.json_response({"status": "ok"})
        record = validate_record(body)
        if isinstance(record, str):
            return error(400, record)
        counters.incr("received")
        await handle(record)
        return web.json_response({"status": "ok"})

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/add", add)
    return app


def create_service(
    config: AggregatorConfig,
    state: AggregatorState,
    outbox: Outbox,
    client: ServiceClient,
    *,
    seed_urls: Sequence[str],
    allowed_hosts: frozenset[str] = frozenset(),
    allowed_ports: frozenset[int] = frozenset(),
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> web.Application:
    """``create_app`` that aggregates, plus the seeding, batching and outbox loops."""
    stats = Stats()
    runtime = AggregatorRuntime(
        config,
        state,
        outbox,
        client,
        Scope(allowed_hosts, config.allowed_suffixes, allowed_ports),
        clock=time.monotonic,
        count=stats.incr,
    )

    async def batch_loop() -> None:
        while True:
            await asyncio.sleep(BATCH_TICK_SECONDS)
            await runtime.tick()

    async def outbox_loop() -> None:
        while True:
            await asyncio.sleep(config.outbox_retry_seconds)
            await runtime.flush()

    async def seed_once() -> None:
        await runtime.seed(seed_urls)

    app = create_app(runtime.handle, stats)

    async def lifetime(_: web.Application) -> AsyncIterator[None]:
        tasks = [
            asyncio.create_task(job())
            for job in (seed_once, batch_loop, outbox_loop, lambda: stats_loop("aggregator", stats))
        ]
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await runtime.shutdown()
        for close in closers:
            await close()

    app.cleanup_ctx.append(lifetime)
    return app
