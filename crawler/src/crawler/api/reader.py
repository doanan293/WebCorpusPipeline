"""reader (block 1, port 6600): ``POST /parse`` takes the downloaded bytes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from aiohttp import web

from crawler.api.common import Stats, error, json_response, stats_loop
from crawler.application.reader.parse import Delivery, DocumentReaders, parse_payload
from crawler.application.reader.ports import MergerClient
from crawler.domain.shared.protocol import ACCEPTED

log = logging.getLogger(__name__)

FORMATS = frozenset({"html", "other", "not_modified"})
# The largest document the downloader forwards (max_file_bytes) plus room for the request itself.
MAX_BODY = 104857600 + 1048576

Handle = Callable[[str, str, str, bytes, Delivery], Awaitable[object]]


def create_app(
    handle: Handle, max_body: int = MAX_BODY, stats: Stats | None = None
) -> web.Application:
    counters = stats or Stats()
    tasks: set[asyncio.Task[None]] = set()

    async def run(url: str, fmt: str, content_type: str, body: bytes, delivery: Delivery) -> None:
        try:
            outcome = await handle(url, fmt, content_type, body, delivery)
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
            return error(400, "X-Format must be html, other or not_modified")
        body = await request.read()
        delivery = Delivery(
            request.headers.get("X-Requested-Url") or url,
            request.headers.get("X-Etag"),
            request.headers.get("X-Last-Modified"),
        )
        if body or fmt == "not_modified":
            counters.incr("received")
            content_type = request.headers.get("Content-Type", "")
            task = asyncio.create_task(run(url, fmt, content_type, body, delivery))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return json_response({"status": ACCEPTED}, status=202)

    app = web.Application(client_max_size=max_body)
    app.router.add_post("/parse", parse)
    return app


def create_service(
    readers: DocumentReaders,
    merger: MergerClient,
    closers: Sequence[Callable[[], Awaitable[None]]] = (),
) -> web.Application:
    stats = Stats()

    async def handle(
        url: str, fmt: str, content_type: str, body: bytes, delivery: Delivery
    ) -> object:
        return await parse_payload(
            url, fmt, content_type, body, readers=readers, merger=merger, delivery=delivery
        )

    app = create_app(handle, stats=stats)

    async def lifetime(_: web.Application) -> AsyncIterator[None]:
        reporter = asyncio.create_task(stats_loop("reader", stats))
        yield
        reporter.cancel()
        for close in closers:
            await close()

    app.cleanup_ctx.append(lifetime)
    return app
