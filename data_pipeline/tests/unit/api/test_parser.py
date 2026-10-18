from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from data_pipeline.api.parser import create_app

HEADERS = {"X-Url": "https://h/a", "X-Format": "html", "Content-Type": "text/html"}


def _post(
    body: bytes, headers: dict[str, str], max_body: int = 1024 * 1024 * 20
) -> tuple[int, list[tuple[str, str, str, int]]]:
    seen: list[tuple[str, str, str, int]] = []
    done = asyncio.Event()

    async def handle(url: str, fmt: str, content_type: str, data: bytes) -> object:
        seen.append((url, fmt, content_type, len(data)))
        done.set()
        return "sent"

    async def scenario() -> int:
        async with TestClient(TestServer(create_app(handle, max_body))) as client:
            response = await client.post("/parse", data=body, headers=headers)
            if response.status == 202 and body:
                await asyncio.wait_for(done.wait(), 2)
            return response.status

    return asyncio.run(scenario()), seen


def test_parse_requires_headers() -> None:
    assert _post(b"<html/>", {"X-Format": "html"})[0] == 400


def test_parse_rejects_unknown_format() -> None:
    assert _post(b"x", {**HEADERS, "X-Format": "image"})[0] == 400


def test_parse_accepts_and_hands_over() -> None:
    status, seen = _post(b"<html/>", HEADERS)
    assert status == 202
    assert seen == [("https://h/a", "html", "text/html", 7)]


def test_parse_accepts_body_larger_than_one_mebibyte() -> None:
    status, seen = _post(
        b"x" * (5 * 1024 * 1024),
        {**HEADERS, "X-Format": "other", "Content-Type": "application/pdf"},
    )
    assert status == 202
    assert seen[0][3] == 5 * 1024 * 1024


def test_parse_empty_body_is_ok() -> None:
    status, seen = _post(b"", HEADERS)
    assert status == 202
    assert seen == []
