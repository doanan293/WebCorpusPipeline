from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from data_pipeline.domain.fetcher.models import Fetched, FetcherConfig, FetchError
from data_pipeline.infrastructure.fetcher.http_getter import AiohttpPageGetter, public_address_check

hits: dict[str, int] = {}


def _site() -> web.Application:
    async def page(_: web.Request) -> web.Response:
        return web.Response(text="<html><body>ok</body></html>", content_type="text/html")

    async def to_private(request: web.Request) -> web.Response:
        raise web.HTTPFound(f"http://127.0.0.1:{request.url.port}/page")

    async def big(_: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/html"})
        await response.prepare(_)
        for _chunk in range(32):
            await response.write(b"x" * 65536)
        await response.write_eof()
        return response

    flaky_left = [2]

    async def flaky(_: web.Request) -> web.Response:
        if flaky_left[0] > 0:
            flaky_left[0] -= 1
            return web.Response(status=503)
        return web.Response(text="<html><body>back</body></html>", content_type="text/html")

    async def missing(_: web.Request) -> web.Response:
        hits["missing"] = hits.get("missing", 0) + 1
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/flaky", flaky)
    app.router.add_get("/missing", missing)
    app.router.add_get("/page", page)
    app.router.add_get("/to-private", to_private)
    app.router.add_get("/big", big)
    return app


async def _reject_ip_literal(url: str) -> None:
    if urlsplit(url).hostname == "127.0.0.1":
        raise FetchError("non_public_address")


def _get(path: str, config: FetcherConfig) -> Fetched:
    async def scenario() -> Fetched:
        async with TestServer(_site(), host="127.0.0.1") as server:
            getter = AiohttpPageGetter(config, check=_reject_ip_literal)
            try:
                return await getter.get(f"http://localhost:{server.port}{path}")
            finally:
                await getter.close()

    return asyncio.run(scenario())


def test_getter_reads_page() -> None:
    page = _get("/page", FetcherConfig())
    assert page.status == 200
    assert b"ok" in page.body


def test_getter_rejects_redirect_to_private_address() -> None:
    with pytest.raises(FetchError, match="non_public_address"):
        _get("/to-private", FetcherConfig())


def test_getter_stops_at_max_bytes() -> None:
    with pytest.raises(FetchError, match="too_large"):
        _get("/big", FetcherConfig(max_response_bytes=1024 * 1024))


def test_public_address_check_rejects_loopback() -> None:
    with pytest.raises(FetchError, match="non_public_address"):
        asyncio.run(public_address_check("http://127.0.0.1/"))


def test_public_address_check_rejects_name_resolving_to_loopback() -> None:
    with pytest.raises(FetchError, match="non_public_address"):
        asyncio.run(public_address_check("http://localhost/"))


def test_retries_on_503_then_succeeds() -> None:
    page = _get("/flaky", FetcherConfig(retries=3, retry_backoff_seconds=0.01))
    assert page.status == 200
    assert b"back" in page.body


def test_no_retry_on_404() -> None:
    hits.clear()
    page = _get("/missing", FetcherConfig(retries=3, retry_backoff_seconds=0.01))
    assert page.status == 404
    assert hits["missing"] == 1


def test_gives_up_after_retries() -> None:
    page = _get("/flaky", FetcherConfig(retries=1, retry_backoff_seconds=0.01))
    assert page.status == 503
