from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.domain.downloader.models import DownloaderConfig, FetchError
from crawler.infra.downloader.browser import PlaywrightRenderer

SHELL = """<html><body><div id="root"></div>
<script>document.getElementById('root').innerText = 'rendered by script';</script></body></html>"""


def test_renderer_returns_html_after_scripts_run() -> None:
    async def page(_: web.Request) -> web.Response:
        return web.Response(text=SHELL, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", page)

    async def scenario() -> bytes:
        async with TestServer(app) as server:
            renderer = PlaywrightRenderer(DownloaderConfig(allow_private_addresses=True))
            try:
                return (await renderer.render(str(server.make_url("/")))).body
            finally:
                await renderer.close()

    assert b"rendered by script" in asyncio.run(scenario())


async def _reject_ip_literal(url: str) -> None:
    if urlsplit(url).hostname == "127.0.0.1":
        raise FetchError("non_public_address")


def _sandbox_site(seen: list[str]) -> web.Application:
    async def page(request: web.Request) -> web.Response:
        port = request.url.port
        body = (
            "<html><body><p>public page</p>"
            f'<img src="http://127.0.0.1:{port}/secret.png">'
            f"<script>fetch('http://127.0.0.1:{port}/add', {{method: 'POST', body: 'x'}})"
            ".catch(() => null);</script></body></html>"
        )
        return web.Response(text=body, content_type="text/html")

    async def private(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(text="private")

    async def jump(request: web.Request) -> web.Response:
        raise web.HTTPFound(f"http://127.0.0.1:{request.url.port}/inside")

    async def hop(request: web.Request) -> web.Response:
        raise web.HTTPFound(f"http://localhost:{request.url.port}/")

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/hop", hop)
    app.router.add_get("/jump", jump)
    app.router.add_route("*", "/secret.png", private)
    app.router.add_route("*", "/add", private)
    app.router.add_get("/inside", private)
    return app


def test_renderer_aborts_private_subresources() -> None:
    seen: list[str] = []

    async def scenario() -> bytes:
        async with TestServer(_sandbox_site(seen), host="127.0.0.1") as server:
            renderer = PlaywrightRenderer(DownloaderConfig(), check=_reject_ip_literal)
            try:
                return (await renderer.render(f"http://localhost:{server.port}/")).body
            finally:
                await renderer.close()

    assert b"public page" in asyncio.run(scenario())
    assert seen == []


def test_renderer_rejects_redirect_to_private_address() -> None:
    seen: list[str] = []

    async def scenario() -> None:
        async with TestServer(_sandbox_site(seen), host="127.0.0.1") as server:
            renderer = PlaywrightRenderer(DownloaderConfig(), check=_reject_ip_literal)
            try:
                await renderer.render(f"http://localhost:{server.port}/jump")
            finally:
                await renderer.close()

    with pytest.raises(FetchError):
        asyncio.run(scenario())
    assert seen == []


def test_renderer_follows_allowed_redirect() -> None:
    seen: list[str] = []

    async def scenario() -> tuple[str, bytes]:
        async with TestServer(_sandbox_site(seen), host="127.0.0.1") as server:
            renderer = PlaywrightRenderer(DownloaderConfig(), check=_reject_ip_literal)
            try:
                page = await renderer.render(f"http://localhost:{server.port}/hop")
                return page.final_url, page.body
            finally:
                await renderer.close()

    final_url, body = asyncio.run(scenario())
    assert final_url.endswith("/")
    assert "/hop" not in final_url
    assert b"public page" in body


def test_renderer_opens_no_websockets() -> None:
    """No socket at all, even where addresses are allowed: page script cannot reach any service."""
    seen: list[str] = []

    async def page(request: web.Request) -> web.Response:
        port = request.url.port
        body = (
            "<html><body><p>public page</p>"
            f"<script>new WebSocket('ws://127.0.0.1:{port}/ws');</script></body></html>"
        )
        return web.Response(text=body, content_type="text/html")

    async def socket(request: web.Request) -> web.WebSocketResponse:
        seen.append(request.path)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/ws", socket)

    async def scenario() -> None:
        async with TestServer(app, host="127.0.0.1") as server:
            renderer = PlaywrightRenderer(DownloaderConfig(allow_private_addresses=True))
            try:
                await renderer.render(f"http://localhost:{server.port}/")
                await asyncio.sleep(0.5)
            finally:
                await renderer.close()

    asyncio.run(scenario())
    assert seen == []
