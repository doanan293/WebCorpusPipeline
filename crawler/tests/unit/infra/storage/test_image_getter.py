from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
from pathlib import Path
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


OPENSSL = shutil.which("openssl")


@pytest.mark.skipif(OPENSSL is None, reason="needs the openssl command")
def test_a_host_whose_certificate_does_not_verify_is_still_read(tmp_path: Path) -> None:
    # Some HUST hosts leave out the intermediate certificate, which browsers fetch and Python does not.
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    assert OPENSSL is not None
    subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)

    async def scenario() -> ImageResult:
        runner = web.AppRunner(_site())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
        await site.start()
        port = runner.addresses[0][1]
        getter = AiohttpImageGetter(timeout_seconds=2, check=_allow)
        try:
            return await getter.get(f"https://localhost:{port}/i.png")
        finally:
            await getter.close()
            await runner.cleanup()

    assert asyncio.run(scenario()) == ImageResult(200, "image/png", b"\x89PNG")
