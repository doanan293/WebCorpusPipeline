from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from crawler.api import mediator as mediator_api
from crawler.api.merger import create_app, create_service
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import parse_seeds
from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue
from crawler.infra.merger.outbox import JsonlOutbox
from crawler.infra.merger.service_client import HttpServiceClient
from crawler.infra.merger.sqlite_state import SqliteMergerState

RECORD: dict[str, Any] = {
    "url": "https://hust.edu.vn/",
    "format": "html",
    "title": "T",
    "content": "c",
    "out-links": [],
}


def _post(body: object) -> tuple[int, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    async def handle(record: dict[str, Any]) -> None:
        seen.append(record)

    async def scenario() -> int:
        async with TestClient(TestServer(create_app(handle))) as client:
            return (await client.post("/add", json=body)).status

    return asyncio.run(scenario()), seen


def test_add_valid_record_ok() -> None:
    status, seen = _post(RECORD)
    assert status == 200
    assert seen[0]["url"] == "https://hust.edu.vn/"


def test_add_invalid_record_400() -> None:
    assert _post({"url": "https://hust.edu.vn/"}) == (400, [])


def test_add_empty_object_is_ok() -> None:
    assert _post({}) == (200, [])


class _Clock:
    def now(self) -> float:
        return time.time()


def _config(tmp_path: Path) -> MergerConfig:
    return MergerConfig(
        database=tmp_path / "agg.sqlite3",
        outbox=tmp_path / "outbox.jsonl",
        batch_seconds=0.05,
        outbox_retry_seconds=0.1,
    )


async def _start_merger(tmp_path: Path, mediator_url: str, seeds: list[str]) -> None:
    config = _config(tmp_path)
    state = SqliteMergerState(config.database)
    client = HttpServiceClient("http://127.0.0.1:9", mediator_url)
    app = create_service(
        config, state, JsonlOutbox(config.outbox), client, seeds=parse_seeds(seeds)
    )
    async with TestServer(app):
        await asyncio.sleep(0.3)
    state.close()


def test_seeds_sent_on_startup(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []

    async def add(request: web.Request) -> web.Response:
        received.append(await request.json())
        return web.json_response({"accepted": 1, "duplicates": 0, "rejected": 0})

    mediator = web.Application()
    mediator.router.add_post("/add", add)

    async def scenario() -> None:
        async with TestServer(mediator) as server:
            await _start_merger(
                tmp_path, str(server.make_url("")).rstrip("/"), ["https://hust.edu.vn/"]
            )

    asyncio.run(scenario())
    assert received == [{"urls": ["https://hust.edu.vn/"]}]


def test_seeds_already_known_are_duplicates(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "mediator.sqlite3")
    seeds = ["https://hust.edu.vn/", "https://soict.hust.edu.vn/"]

    async def scenario() -> None:
        async with TestServer(mediator_api.create_app(queue, _Clock())) as server:
            url = str(server.make_url("")).rstrip("/")
            await _start_merger(tmp_path, url, seeds)
            queue.mark_sent("https://hust.edu.vn/", time.time(), 9e12)
            await _start_merger(tmp_path, url, seeds)

    asyncio.run(scenario())
    assert queue.counts(time.time()).total == 2
    assert queue.next_due(time.time(), ()) is not None
    row = queue.db.execute(
        "SELECT next_due_at FROM urls WHERE url = 'https://hust.edu.vn/'"
    ).fetchone()
    assert row == (9e12,)


def test_add_accepts_a_not_modified_notice() -> None:
    notice = {
        "url": "https://hust.edu.vn/a",
        "requested_url": "https://hust.edu.vn/a",
        "format": "not_modified",
    }
    status, seen = _post(notice)
    assert status == 200
    assert seen == [notice]


def _crawl_service(tmp_path: Path) -> web.Application:
    config = _config(tmp_path)
    client = HttpServiceClient("http://127.0.0.1:9", "http://127.0.0.1:9")
    return create_service(
        config,
        SqliteMergerState(config.database),
        JsonlOutbox(config.outbox),
        client,
        seeds=parse_seeds([]),
        closers=(client.close,),
    )


def _crawl(tmp_path: Path, body: object) -> tuple[int, dict[str, Any]]:
    async def scenario() -> tuple[int, dict[str, Any]]:
        async with TestClient(TestServer(_crawl_service(tmp_path))) as client:
            response = await client.post("/crawl", json=body)
            return response.status, await response.json()

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        {"url": "https://b.hust.edu.vn/", "max_pages": 0},
        {"url": "https://b.hust.edu.vn/", "max_pages": -1},
        {"url": "https://b.hust.edu.vn/", "max_pages": "5"},
        {"url": "https://b.hust.edu.vn/", "max_pages": True},
        {"url": "ftp://b.hust.edu.vn/"},
        {"url": 5},
        {},
    ],
)
def test_crawl_bad_input_is_400(tmp_path: Path, body: dict[str, Any]) -> None:
    status, answer = _crawl(tmp_path, body)
    assert status == 400
    assert answer["error"]


def test_crawl_returns_status(tmp_path: Path) -> None:
    status, answer = _crawl(tmp_path, {"url": "https://b.hust.edu.vn/", "max_pages": 3})
    assert status == 200
    assert answer["job"]
    assert answer["host"] == "b.hust.edu.vn"
    assert (answer["max_pages"], answer["queued"], answer["saved"]) == (3, 1, 0)
    assert (answer["stopped"], answer["done"]) == (False, False)


def test_crawl_status_and_stop(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int, dict[str, Any], int]:
        async with TestClient(TestServer(_crawl_service(tmp_path))) as client:
            started = await (
                await client.post("/crawl", json={"url": "https://b.hust.edu.vn/"})
            ).json()
            job = started["job"]
            found = (await client.get(f"/crawl/{job}")).status
            missing = (await client.get("/crawl/nope")).status
            stopped = await (await client.post(f"/crawl/{job}/stop")).json()
            stop_missing = (await client.post("/crawl/nope/stop")).status
            return found, missing, stopped, stop_missing

    found, missing, stopped, stop_missing = asyncio.run(scenario())
    assert (found, missing, stop_missing) == (200, 404, 404)
    assert (stopped["stopped"], stopped["done"]) == (True, True)
