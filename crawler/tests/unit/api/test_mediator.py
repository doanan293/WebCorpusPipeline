from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from crawler.api.mediator import create_app
from tests.fakes.mediator import FakeClock, FakeUrlQueue


def _post(queue: FakeUrlQueue, body: str) -> tuple[int, object]:
    async def scenario() -> tuple[int, object]:
        async with TestClient(TestServer(create_app(queue, FakeClock(0.0)))) as client:
            response = await client.post("/add", data=body)
            return response.status, await response.json()

    return asyncio.run(scenario())


def test_add_returns_counts() -> None:
    queue = FakeUrlQueue()
    assert _post(queue, '{"urls": ["https://hust.edu.vn/a", "x"]}') == (
        200,
        {"accepted": 1, "duplicates": 0, "rejected": 1},
    )
    assert list(queue.rows) == ["https://hust.edu.vn/a"]


def test_add_empty_list_is_ok() -> None:
    assert _post(FakeUrlQueue(), '{"urls": []}') == (
        200,
        {"accepted": 0, "duplicates": 0, "rejected": 0},
    )


@pytest.mark.parametrize("body", ['{"urls": "a"}', '{"urls": [1]}', "{}", "not json"])
def test_add_bad_body_is_400(body: str) -> None:
    queue = FakeUrlQueue()
    status, answer = _post(queue, body)
    assert status == 400
    assert isinstance(answer, dict) and "error" in answer
    assert queue.rows == {}


def test_report_updates_schedule() -> None:
    queue = FakeUrlQueue()
    queue.add([("https://hust.edu.vn/a", "hust.edu.vn", "page")], 0.0)

    async def scenario() -> list[tuple[int, object]]:
        async with TestClient(TestServer(create_app(queue, FakeClock(0.0)))) as client:
            good = await client.post(
                "/report",
                json={
                    "reports": [
                        {
                            "url": "https://hust.edu.vn/a",
                            "change": "unchanged",
                            "etag": None,
                            "last_modified": None,
                        }
                    ]
                },
            )
            bad = await client.post("/report", json={"reports": [{"url": 1}]})
            return [
                (good.status, await good.json()),
                (bad.status, (await bad.json()).keys() and "error"),
            ]

    assert asyncio.run(scenario()) == [(200, {"updated": 1}), (400, "error")]
    row = queue.get("https://hust.edu.vn/a")
    assert row is not None and row.interval_days == 14.0


def test_add_force_must_be_boolean() -> None:
    status, _ = _post(FakeUrlQueue(), '{"urls": ["https://hust.edu.vn/a"], "force": "yes"}')
    assert status == 400


def test_add_force_requeues_known_url() -> None:
    queue = FakeUrlQueue()
    _post(queue, '{"urls": ["https://hust.edu.vn/a"]}')
    queue.mark_sent("https://hust.edu.vn/a", 0.0, 99.0)
    status, _ = _post(queue, '{"urls": ["https://hust.edu.vn/a"], "force": true}')
    assert status == 200
    assert queue.rows["https://hust.edu.vn/a"].next_due_at == 0.0


def test_add_cancel_takes_url_off_the_schedule() -> None:
    queue = FakeUrlQueue()
    _post(queue, '{"urls": ["https://hust.edu.vn/a"]}')
    status, answer = _post(queue, '{"urls": ["https://hust.edu.vn/a"], "cancel": true}')
    assert (status, answer) == (200, {"cancelled": 1})
    assert queue.rows["https://hust.edu.vn/a"].next_due_at is None


def test_add_cancel_must_be_boolean() -> None:
    status, _ = _post(FakeUrlQueue(), '{"urls": [], "cancel": "yes"}')
    assert status == 400
