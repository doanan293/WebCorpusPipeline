from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from crawler.api.downloader import create_app
from crawler.application.downloader.fetch import FetchOutcome
from crawler.domain.downloader.models import Validators


def test_ping_idle() -> None:
    async def work(_: str, __: Validators | None) -> FetchOutcome:
        return "forwarded"

    async def scenario() -> tuple[int, object]:
        async with TestClient(TestServer(create_app(work))) as client:
            response = await client.get("/ping")
            return response.status, await response.json()

    assert asyncio.run(scenario()) == (200, {"status": "đang chờ"})


def test_load_accepts_and_turns_busy() -> None:
    gate = asyncio.Event()
    seen: list[str] = []

    async def work(url: str, _: Validators | None) -> FetchOutcome:
        seen.append(url)
        await gate.wait()
        return "forwarded"

    async def scenario() -> list[object]:
        async with TestClient(TestServer(create_app(work))) as client:
            load = await client.post("/load", json={"url": "https://hust.edu.vn/a"})
            answers: list[object] = [load.status, await load.json()]
            answers.append(await (await client.get("/ping")).json())
            gate.set()
            for _ in range(50):
                status = await (await client.get("/ping")).json()
                if status == {"status": "đang chờ"}:
                    break
                await asyncio.sleep(0.01)
            answers.append(status)
            return answers

    assert asyncio.run(scenario()) == [
        202,
        {"status": "đã nhận"},
        {"status": "đang bận"},
        {"status": "đang chờ"},
    ]
    assert seen == ["https://hust.edu.vn/a"]


def test_second_load_while_busy_is_409() -> None:
    gate = asyncio.Event()

    async def work(_: str, __: Validators | None) -> FetchOutcome:
        await gate.wait()
        return "forwarded"

    async def scenario() -> list[tuple[int, object]]:
        async with TestClient(TestServer(create_app(work))) as client:

            async def load(url: str) -> tuple[int, object]:
                response = await client.post("/load", json={"url": url})
                return response.status, await response.json()

            answers = await asyncio.gather(load("https://h/1"), load("https://h/2"))
            gate.set()
            return sorted(answers, key=lambda answer: answer[0])

    assert asyncio.run(scenario()) == [(202, {"status": "đã nhận"}), (409, {"status": "đang bận"})]


def test_load_bad_body_is_400() -> None:
    async def work(_: str, __: Validators | None) -> FetchOutcome:
        return "forwarded"

    async def scenario() -> list[int]:
        async with TestClient(TestServer(create_app(work))) as client:
            return [
                (await client.post("/load", data=body)).status
                for body in ('{"url": 1}', "{}", "[]", '{"url": ""}')
            ]

    assert asyncio.run(scenario()) == [400, 400, 400, 400]


def test_work_crash_returns_to_idle() -> None:
    async def work(_: str, __: Validators | None) -> FetchOutcome:
        raise RuntimeError("boom")

    async def scenario() -> object:
        async with TestClient(TestServer(create_app(work))) as client:
            await client.post("/load", json={"url": "https://h/1"})
            for _ in range(50):
                status = await (await client.get("/ping")).json()
                if status == {"status": "đang chờ"}:
                    return status
                await asyncio.sleep(0.01)
            return status

    assert asyncio.run(scenario()) == {"status": "đang chờ"}


def test_ping_body_is_plain_utf8() -> None:
    async def work(_: str, __: Validators | None) -> FetchOutcome:
        return "forwarded"

    async def scenario() -> str:
        async with TestClient(TestServer(create_app(work))) as client:
            return await (await client.get("/ping")).text()

    assert '"đang chờ"' in asyncio.run(scenario())


def test_load_passes_validators_to_the_work() -> None:
    seen: list[Validators | None] = []
    done = asyncio.Event()

    async def work(_: str, validators: Validators | None) -> FetchOutcome:
        seen.append(validators)
        done.set()
        return "not_modified"

    async def scenario() -> None:
        async with TestClient(TestServer(create_app(work))) as client:
            body = {"url": "https://h/a", "etag": '"e"', "last_modified": "Mon"}
            assert (await client.post("/load", json=body)).status == 202
            await asyncio.wait_for(done.wait(), 2)
            await client.post("/load", json={"url": "https://h/b"})

    asyncio.run(scenario())
    assert seen[0] == Validators('"e"', "Mon")
