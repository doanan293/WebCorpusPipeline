from __future__ import annotations

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from data_pipeline.domain.fetcher.models import FetcherConfig
from data_pipeline.infrastructure.fetcher.browser import PlaywrightRenderer

SHELL = """<html><body><div id="root"></div>
<script>document.getElementById('root').innerText = 'rendered by script';</script></body></html>"""


def test_renderer_returns_html_after_scripts_run() -> None:
    async def page(_: web.Request) -> web.Response:
        return web.Response(text=SHELL, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", page)

    async def scenario() -> bytes:
        async with TestServer(app) as server:
            renderer = PlaywrightRenderer(FetcherConfig())
            try:
                return (await renderer.render(str(server.make_url("/")))).body
            finally:
                await renderer.close()

    assert b"rendered by script" in asyncio.run(scenario())
