"""Lưu trữ (block 5, port 2200): ``POST /add`` writes one record into Mongo."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from aiohttp import web

from data_pipeline.application.storage.ports import CorpusStore, StoreUnavailableError
from data_pipeline.application.storage.store import store_record
from data_pipeline.domain.shared.protocol import validate_record
from dich_vu.api.common import Stats, error, json_object, stats_loop

log = logging.getLogger(__name__)


def create_app(
    store: CorpusStore, now: Callable[[], str], stats: Stats | None = None
) -> web.Application:
    counters = stats or Stats()

    async def add(request: web.Request) -> web.Response:
        body = await json_object(request)
        if not body:
            return web.json_response({"status": "ok"})
        record = validate_record(body)
        if isinstance(record, str):
            return error(400, record)
        try:
            await asyncio.to_thread(store_record, record, store, now())
        except StoreUnavailableError:
            counters.incr("unavailable")
            return error(503, "mongo unavailable")
        counters.incr("stored")
        return web.json_response({"status": "ok"})

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/add", add)
    return app


def create_service(store: CorpusStore, now: Callable[[], str]) -> web.Application:
    stats = Stats()
    app = create_app(store, now, stats)

    async def lifetime(_: web.Application) -> AsyncIterator[None]:
        try:
            await asyncio.to_thread(store.ensure_indexes)
        except StoreUnavailableError:
            log.warning("mongo unavailable at start; indexes will be created on a later start")
        reporter = asyncio.create_task(stats_loop("storage", stats))
        yield
        reporter.cancel()

    app.cleanup_ctx.append(lifetime)
    return app
