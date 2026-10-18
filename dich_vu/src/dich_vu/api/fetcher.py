"""Tải về (block 2): ``POST /load`` takes one URL at a time; ``GET /ping`` says if it is free."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from aiohttp import web

from data_pipeline.application.fetcher.fetch import FetchOutcome, fetch_and_forward
from data_pipeline.application.fetcher.ports import PageGetter, ParserClient, Renderer
from data_pipeline.domain.shared.protocol import ACCEPTED, BUSY, IDLE
from dich_vu.api.common import Stats, error, json_object, json_response, stats_loop

log = logging.getLogger(__name__)


def create_app(
    work: Callable[[str], Awaitable[FetchOutcome]], stats: Stats | None = None
) -> web.Application:
    counters = stats or Stats()
    state = {"busy": False}
    tasks: set[asyncio.Task[None]] = set()

    async def run(url: str) -> None:
        try:
            outcome = await work(url)
            log.info("fetch %s: %s", url, outcome)
            counters.incr(outcome)
        except Exception:
            log.exception("fetch %s crashed", url)
            counters.incr("crashed")
        finally:
            state["busy"] = False

    async def load(request: web.Request) -> web.Response:
        body = await json_object(request)
        url = body.get("url")
        if not isinstance(url, str) or not url:
            return error(400, "url must be a non-empty string")
        if state["busy"]:
            return json_response({"status": BUSY}, status=409)
        # Set before any await, so a second /load in flight sees the node as busy.
        state["busy"] = True
        counters.incr("received")
        task = asyncio.create_task(run(url))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return json_response({"status": ACCEPTED}, status=202)

    async def ping(_: web.Request) -> web.Response:
        return json_response({"status": BUSY if state["busy"] else IDLE})

    app = web.Application()
    app.router.add_post("/load", load)
    app.router.add_get("/ping", ping)
    return app


def create_service(
    getter: PageGetter,
    renderer: Renderer,
    parser: ParserClient,
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> web.Application:
    """``create_app`` doing the real fetch; logs stats every 30 s and closes adapters at exit."""
    stats = Stats()

    async def work(url: str) -> FetchOutcome:
        return await fetch_and_forward(url, getter, renderer, parser)

    app = create_app(work, stats)

    async def lifetime(_: web.Application) -> AsyncIterator[None]:
        reporter = asyncio.create_task(stats_loop("fetcher", stats))
        yield
        reporter.cancel()
        for close in (renderer.close, *closers):
            await close()

    app.cleanup_ctx.append(lifetime)
    return app
