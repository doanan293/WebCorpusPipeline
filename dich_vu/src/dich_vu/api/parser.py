"""Đọc nội dung (block 1, port 6600): ``POST /parse`` takes the downloaded bytes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from aiohttp import web

from data_pipeline.application.parser.parse import DocumentReaders, parse_payload
from data_pipeline.application.parser.ports import AggregatorClient
from data_pipeline.domain.shared.protocol import ACCEPTED
from dich_vu.api.common import Stats, error, stats_loop

log = logging.getLogger(__name__)

FORMATS = frozenset({"html", "other"})
# The largest document the fetcher forwards (max_file_bytes) plus room for the request itself.
MAX_BODY = 104857600 + 1048576

Handle = Callable[[str, str, str, bytes], Awaitable[object]]


def create_app(
    handle: Handle, max_body: int = MAX_BODY, stats: Stats | None = None
) -> web.Application:
    counters = stats or Stats()
    tasks: set[asyncio.Task[None]] = set()

    async def run(url: str, fmt: str, content_type: str, body: bytes) -> None:
        try:
            outcome = await handle(url, fmt, content_type, body)
            log.info("parse %s: %s", url, outcome)
            counters.incr(str(outcome))
        except Exception:
            log.exception("parse %s crashed", url)
            counters.incr("crashed")

    async def parse(request: web.Request) -> web.Response:
        url = request.headers.get("X-Url", "")
        fmt = request.headers.get("X-Format", "")
        if not url:
            return error(400, "X-Url header is required")
        if fmt not in FORMATS:
            return error(400, "X-Format must be html or other")
        body = await request.read()
        if body:
            counters.incr("received")
            task = asyncio.create_task(run(url, fmt, request.headers.get("Content-Type", ""), body))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return web.json_response({"status": ACCEPTED}, status=202)

    app = web.Application(client_max_size=max_body)
    app.router.add_post("/parse", parse)
    return app


def create_service(
    readers: DocumentReaders,
    aggregator: AggregatorClient,
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> web.Application:
    stats = Stats()

    async def handle(url: str, fmt: str, content_type: str, body: bytes) -> object:
        return await parse_payload(
            url, fmt, content_type, body, readers=readers, aggregator=aggregator
        )

    app = create_app(handle, stats=stats)

    async def lifetime(_: web.Application) -> AsyncIterator[None]:
        reporter = asyncio.create_task(stats_loop("parser", stats))
        yield
        reporter.cancel()
        for close in closers:
            await close()

    app.cleanup_ctx.append(lifetime)
    return app
