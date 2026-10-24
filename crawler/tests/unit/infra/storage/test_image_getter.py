from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.application.storage.ports import ImageFetchError, ImageResult
from crawler.domain.storage.pictures import MAX_PICTURE_BYTES
from crawler.infra.storage.image_getter import AiohttpImageGetter, Check


def _site() -> web.Application:
    async def image(_: web.Request) -> web.Response:
        return web.Response(body=b"\x89PNG", content_type="image/png")

    async def huge(_: web.Request) -> web.Response:
        return web.Response(body=b"x" * (MAX_PICTURE_BYTES + 5000), content_type="image/jpeg")

    async def slow(_: web.Request) -> web.Response:
        await asyncio.sleep(2)
        return web.Response(body=b"late", content_type="image/png")

    async def to_private(request: web.Request) -> web.Response:
        raise web.HTTPFound(f"http://127.0.0.1:{request.url.port}/i.png")

    app = web.Application()
    app.router.add_get("/to-private", to_private)
    app.router.add_get("/i.png", image)
    app.router.add_get("/huge.jpg", huge)
    app.router.add_get("/slow.png", slow)
    return app


async def _allow(_: str) -> None:
    return None


async def _reject_ip_literal(url: str) -> None:
    if urlsplit(url).hostname == "127.0.0.1":
        raise ImageFetchError("non_public_address")


def _get(path: str, check: Check = _allow, host: str = "127.0.0.1") -> ImageResult:
    async def scenario() -> ImageResult:
        async with TestServer(_site(), host="127.0.0.1") as server:
            getter = AiohttpImageGetter(timeout_seconds=0.5, check=check)
            try:
                return await getter.get(f"http://{host}:{server.port}{path}")
            finally:
                await getter.close()

    return asyncio.run(scenario())


def test_image_is_read() -> None:
    assert _get("/i.png") == ImageResult(200, "image/png", b"\x89PNG")


def test_reading_stops_past_the_limit() -> None:
    assert len(_get("/huge.jpg").body) == MAX_PICTURE_BYTES + 1


def test_timeout_is_a_fetch_error() -> None:
    with pytest.raises(ImageFetchError, match="timeout"):
        _get("/slow.png")


def test_default_check_refuses_a_private_address() -> None:
    with pytest.raises(ImageFetchError, match="non_public_address"):
        asyncio.run(AiohttpImageGetter().get("http://127.0.0.1/x.png"))


def test_redirect_to_a_private_address_is_refused() -> None:
    with pytest.raises(ImageFetchError, match="non_public_address"):
        _get("/to-private", check=_reject_ip_literal, host="localhost")
