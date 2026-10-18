from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from dieu_khien.application.controller.add_urls import add_urls
from dieu_khien.application.controller.dispatch import Dispatcher
from dieu_khien.domain.controller.models import ControllerConfig
from dieu_khien.domain.shared.protocol import BUSY, IDLE
from tests.fakes.controller import FakeClock, FakeDownloader, FakeUrlQueue

DAY = 86400.0


def _setup(
    nodes: dict[str, str | None], urls: list[str], load_ok: dict[str, bool] | None = None
) -> tuple[Dispatcher, FakeUrlQueue, FakeDownloader, FakeClock]:
    queue, clock = FakeUrlQueue(), FakeClock(100.0)
    downloader = FakeDownloader(nodes, load_ok)
    config = ControllerConfig(downloaders=tuple(nodes), database=Path("unused"))
    add_urls(queue, urls, clock)
    return Dispatcher(queue, downloader, clock, config), queue, downloader, clock


def _steps(dispatcher: Dispatcher, count: int) -> list[bool]:
    async def scenario() -> list[bool]:
        return [await dispatcher.step() for _ in range(count)]

    return asyncio.run(scenario())


def test_dispatch_round_robins_idle_nodes() -> None:
    u1, u2 = "https://a.hust.edu.vn/1", "https://b.hust.edu.vn/1"
    dispatcher, _, downloader, _ = _setup({"a": IDLE, "b": IDLE}, [u1, u2])
    assert _steps(dispatcher, 3) == [True, True, False]
    assert sorted(node for node, _ in downloader.loads) == ["a", "b"]
    assert {url for _, url in downloader.loads} == {u1, u2}


def test_dispatch_respects_host_delay() -> None:
    dispatcher, _, downloader, clock = _setup(
        {"a": IDLE}, ["https://a.hust.edu.vn/1", "https://a.hust.edu.vn/2"]
    )
    assert _steps(dispatcher, 1) == [True]
    clock.t += 0.5
    assert _steps(dispatcher, 1) == [False]
    clock.t += 0.5
    assert _steps(dispatcher, 1) == [True]
    assert len(downloader.loads) == 2


def test_dispatch_skips_busy_and_rests_dead_node() -> None:
    dispatcher, _, downloader, clock = _setup(
        {"a": None, "b": BUSY, "c": IDLE}, ["https://a.hust.edu.vn/1"]
    )
    assert _steps(dispatcher, 1) == [True]
    assert downloader.loads == [("c", "https://a.hust.edu.vn/1")]
    assert dispatcher.rotation.resting["a"] == clock.t + 30.0
    assert "b" not in dispatcher.rotation.resting


def test_dispatch_failed_load_keeps_url_due() -> None:
    url = "https://a.hust.edu.vn/1"
    dispatcher, queue, _, clock = _setup({"a": IDLE}, [url], load_ok={"a": False})
    assert _steps(dispatcher, 1) == [False]
    assert queue.rows[url].attempts == 1
    assert queue.rows[url].next_due_at == clock.t
    assert dispatcher.rotation.resting["a"] == clock.t + 30.0


def test_dispatch_sets_recrawl_after_success() -> None:
    url = "https://a.hust.edu.vn/1"
    dispatcher, queue, _, clock = _setup({"a": IDLE}, [url])
    _steps(dispatcher, 1)
    assert queue.rows[url].next_due_at == clock.t + 7 * DAY
    assert queue.rows[url].last_sent_at == clock.t


def test_dispatch_does_not_ping_without_due_url() -> None:
    dispatcher, _, downloader, _ = _setup({"a": IDLE}, [])
    assert _steps(dispatcher, 1) == [False]
    assert downloader.pings == []


def test_unexpected_ping_status_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    dispatcher, _, downloader, _ = _setup({"a": "dang cho"}, ["https://a.hust.edu.vn/1"])
    with caplog.at_level(logging.WARNING):
        assert _steps(dispatcher, 1) == [False]
    assert downloader.loads == []
    assert "unexpected ping status" in caplog.text
    assert "dang cho" in caplog.text
