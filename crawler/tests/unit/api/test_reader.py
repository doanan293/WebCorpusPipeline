from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from crawler.api.reader import create_app
from crawler.application.reader.parse import Delivery

HEADERS = {"X-Url": "https://h/a", "X-Format": "html", "Content-Type": "text/html"}


DELIVERIES: list[Delivery] = []


def _post(
    body: bytes, headers: dict[str, str], max_body: int = 1024 * 1024 * 20
) -> tuple[int, list[tuple[str, str, str, int]]]:
    seen: list[tuple[str, str, str, int]] = []
    done = asyncio.Event()

    async def handle(
        url: str, fmt: str, content_type: str, data: bytes, delivery: Delivery
    ) -> object:
        seen.append((url, fmt, content_type, len(data)))
        DELIVERIES.append(delivery)
        done.set()
        return "sent"

    async def scenario() -> int:
        async with TestClient(TestServer(create_app(handle, max_body))) as client:
            response = await client.post("/parse", data=body, headers=headers)
            if response.status == 202 and (body or headers.get("X-Format") == "not_modified"):
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


def test_parse_passes_delivery_headers() -> None:
    headers = {
        **HEADERS,
        "X-Requested-Url": "https://h/a?x=1",
        "X-Etag": '"e"',
        "X-Last-Modified": "Mon",
    }
    assert _post(b"<html/>", headers)[0] == 202
    assert DELIVERIES[-1] == Delivery("https://h/a?x=1", '"e"', "Mon")


def test_parse_accepts_not_modified_without_body() -> None:
    status, seen = _post(b"", {"X-Url": "https://h/a", "X-Format": "not_modified"})
    assert status == 202
    assert [(url, fmt, size) for url, fmt, _, size in seen] == [("https://h/a", "not_modified", 0)]
    assert DELIVERIES[-1] == Delivery("https://h/a", None, None)
