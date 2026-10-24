"""mediator (block 3, port 5200): ``POST /add``, ``POST /report`` and the loop feeding the nodes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Mapping

from aiohttp import web

from crawler.api.common import error, json_object, json_response
from crawler.application.mediator.add_urls import add_urls
from crawler.application.mediator.dispatch import Dispatcher, run_dispatch
from crawler.application.mediator.ports import Clock, Downloader, UrlQueue
from crawler.application.mediator.report import report_outcomes, valid_report
from crawler.domain.mediator.models import DEFAULT_RECRAWL_DAYS, MediatorConfig, UrlKind

log = logging.getLogger(__name__)

SUMMARY_SECONDS = 30.0


def create_app(
    queue: UrlQueue,
    clock: Clock,
    recrawl_days: Mapping[UrlKind, float] = DEFAULT_RECRAWL_DAYS,
) -> web.Application:
    async def add(request: web.Request) -> web.Response:
        body = await json_object(request)
        urls = body.get("urls")
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            return error(400, "urls must be a list of strings")
        result = add_urls(queue, urls, clock)
        return json_response(
            {
                "accepted": result.accepted,
                "duplicates": result.duplicates,
                "rejected": result.rejected,
            }
        )

    async def report(request: web.Request) -> web.Response:
        """What each visit found (new, modified, unchanged, not_modified), from the merger."""
        body = await json_object(request)
        reports = body.get("reports")
        if not isinstance(reports, list) or not all(valid_report(item) for item in reports):
            return error(400, "reports must be a list of {url, change, etag, last_modified}")
        return json_response({"updated": report_outcomes(queue, reports, recrawl_days, clock)})

    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_post("/add", add)
    app.router.add_post("/report", report)
    return app


def create_service(
    queue: UrlQueue, downloader: Downloader, clock: Clock, config: MediatorConfig
) -> web.Application:
    """``create_app`` with the dispatch and summary loops running for the app's lifetime."""
    app = create_app(queue, clock, config.recrawl_days)
    dispatcher = Dispatcher(queue, downloader, clock, config)

    async def background(_: web.Application) -> AsyncIterator[None]:
        tasks = [
            asyncio.create_task(run_dispatch(dispatcher, config.tick_seconds)),
            asyncio.create_task(summary_loop(queue, dispatcher, clock)),
        ]
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await downloader.close()

    app.cleanup_ctx.append(background)
    return app


async def summary_loop(
    queue: UrlQueue, dispatcher: Dispatcher, clock: Clock, every: float = SUMMARY_SECONDS
) -> None:
    while True:
        await asyncio.sleep(every)
        now = clock.now()
        counts = queue.counts(now)
        nodes = len(dispatcher.config.downloaders)
        resting = nodes - len(dispatcher.rotation.order(now))
        log.info(
            "mediator: %d urls, %d due, %d in flight, %d/%d nodes resting",
            counts.total,
            counts.due,
            dispatcher.in_flight(),
            resting,
            nodes,
        )
