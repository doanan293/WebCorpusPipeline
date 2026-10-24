from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from data_pipeline.domain.merger.config import MergerConfig
from data_pipeline.domain.merger.seeds import parse_seeds
from data_pipeline.infrastructure.mediator.sqlite_queue import SqliteUrlQueue
from data_pipeline.infrastructure.merger.outbox import JsonlOutbox
from data_pipeline.infrastructure.merger.service_client import HttpServiceClient
from data_pipeline.infrastructure.merger.sqlite_state import SqliteMergerState
from services.api import mediator as mediator_api
from services.api.merger import create_app, create_service

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
