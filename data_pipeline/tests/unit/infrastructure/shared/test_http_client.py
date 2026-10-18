from __future__ import annotations

import asyncio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from data_pipeline.infrastructure.shared.http_client import send


def _server(statuses: list[int], seen: list[bytes]) -> web.Application:
    async def handle(request: web.Request) -> web.Response:
        seen.append(await request.read())
        return web.Response(status=statuses.pop(0) if len(statuses) > 1 else statuses[0])

    app = web.Application()
    app.router.add_post("/add", handle)
    return app


def _run(statuses: list[int], **kwargs: object) -> tuple[bool, list[bytes], list[float]]:
    seen: list[bytes] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> bool:
        async with (
            TestServer(_server(statuses, seen)) as server,
            aiohttp.ClientSession() as session,
        ):
            return await send(session, str(server.make_url("/add")), sleep=fake_sleep, **kwargs)  # type: ignore[arg-type]

    return asyncio.run(scenario()), seen, sleeps


def test_send_retries_5xx_then_succeeds() -> None:
    ok, seen, sleeps = _run([503, 503, 200], json={"urls": []})
    assert ok
    assert len(seen) == 3
    assert seen[0] == b'{"urls": []}'
    assert sleeps == [1.0, 2.0]


def test_send_does_not_retry_4xx() -> None:
    ok, seen, sleeps = _run([400], json={})
    assert not ok
    assert len(seen) == 1
    assert sleeps == []


def test_send_gives_up_after_backoff() -> None:
    ok, seen, sleeps = _run([500], data=b"x")
    assert not ok
    assert len(seen) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_send_retries_when_nobody_listens() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> bool:
        async with aiohttp.ClientSession() as session:
            return await send(session, "http://127.0.0.1:9/add", json={}, sleep=fake_sleep)

    assert asyncio.run(scenario()) is False
    assert sleeps == [1.0, 2.0, 4.0]
