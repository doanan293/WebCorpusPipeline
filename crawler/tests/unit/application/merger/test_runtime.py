from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any, override

import pytest

from crawler.application.merger.aggregate import Scope
from crawler.application.merger.delivery import deliver, flush_outbox
from crawler.application.merger.ports import SendResult, Target
from crawler.application.merger.runtime import MergerRuntime
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import parse_seeds
from tests.fakes.merger import FakeServiceClient, MemoryMergerState, MemoryOutbox

HOST = "a.hust.edu.vn"


def _record(url: str, links: list[str]) -> dict[str, Any]:
    return {
        "url": url,
        "format": "html",
        "title": "t",
        "content": f"text of {url}",
        "out-links": [[link, "x"] for link in links],
    }


def _runtime(client: FakeServiceClient, outbox: MemoryOutbox, clock: list[float]) -> MergerRuntime:
    return MergerRuntime(
        MergerConfig(batch_size=500, batch_seconds=1.0, database=Path("unused")),
        MemoryMergerState(),
        outbox,
        client,
        Scope(frozenset({HOST})),
        clock=lambda: clock[0],
    )


def test_rejected_record_goes_to_dead_letters() -> None:
    outbox = MemoryOutbox()
    client = FakeServiceClient(answer=lambda target, payload: "dead")
    asyncio.run(deliver("storage", {"url": "big"}, client=client, outbox=outbox))
    assert outbox.entries == []
    assert outbox.dead_letters == [("storage", {"url": "big"}, "rejected")]


def test_dead_entry_does_not_block_later_entries() -> None:
    outbox = MemoryOutbox()
    outbox.append("storage", {"url": "big"})
    outbox.append("storage", {"url": "small"})

    def answer(target: Target, payload: dict[str, Any]) -> SendResult:
        return "dead" if payload["url"] == "big" else "ok"

    client = FakeServiceClient(answer=answer)
    assert asyncio.run(flush_outbox(client, outbox)) == 1
    assert outbox.entries == []
    assert [payload for _, payload, _ in outbox.dead_letters] == [{"url": "big"}]
    assert client.sent == [("storage", {"url": "small"})]


def test_retry_on_storage_does_not_block_mediator_entries() -> None:
    outbox = MemoryOutbox()
    outbox.append("storage", {"url": "u1"})
    outbox.append("mediator", {"urls": ["u2"]})
    outbox.append("storage", {"url": "u3"})

    def answer(target: Target, payload: dict[str, Any]) -> SendResult:
        return "retry" if target == "storage" else "ok"

    client = FakeServiceClient(answer=answer)
    assert asyncio.run(flush_outbox(client, outbox)) == 1
    assert [entry.payload for entry in outbox.entries] == [{"url": "u1"}, {"url": "u3"}]
    # Storage is down: after its first failure the rest of its entries wait, unsent.
    assert [target for target, _ in client.attempts].count("storage") == 1


def test_tick_survives_client_exception(caplog: pytest.LogCaptureFixture) -> None:
    clock = [0.0]
    client = FakeServiceClient(error=RuntimeError("boom"))
    runtime = _runtime(client, MemoryOutbox(), clock)
    runtime.batcher.add([f"https://{HOST}/1"], 0.0)
    clock[0] = 2.0
    with caplog.at_level(logging.ERROR):
        asyncio.run(runtime.tick())
    assert "boom" in caplog.text


def test_flush_survives_client_exception(caplog: pytest.LogCaptureFixture) -> None:
    outbox = MemoryOutbox()
    outbox.append("storage", {"url": "u"})
    runtime = _runtime(FakeServiceClient(error=RuntimeError("boom")), outbox, [0.0])
    with caplog.at_level(logging.ERROR):
        asyncio.run(runtime.flush())
    assert "boom" in caplog.text
    assert len(outbox.entries) == 1


def test_shutdown_flushes_pending_urls_to_outbox() -> None:
    outbox = MemoryOutbox()
    clock = [0.0]
    runtime = _runtime(FakeServiceClient(), outbox, clock)
    asyncio.run(runtime.handle(_record(f"https://{HOST}/", [f"https://{HOST}/new"])))
    assert runtime.batcher.pending == [f"https://{HOST}/new"]
    asyncio.run(runtime.shutdown())
    assert [(e.target, e.payload) for e in outbox.entries] == [
        ("mediator", {"urls": [f"https://{HOST}/new"]})
    ]
    assert runtime.batcher.pending == []


def test_seed_goes_to_mediator() -> None:
    client = FakeServiceClient()
    runtime = _runtime(client, MemoryOutbox(), [0.0])
    asyncio.run(runtime.seed([f"https://{HOST}/"]))
    assert client.sent == [("mediator", {"urls": [f"https://{HOST}/"]})]


def test_url_seed_spreads_within_its_host() -> None:
    client = FakeServiceClient()
    runtime = MergerRuntime(
        MergerConfig(database=Path("unused")),
        MemoryMergerState(),
        MemoryOutbox(),
        client,
        Scope(frozenset()),
        clock=lambda: 0.0,
    )
    seed = "https://soict.hust.edu.vn/tin-tuc/abc"
    asyncio.run(runtime.add_seeds(parse_seeds([seed])))
    assert client.sent == [("mediator", {"urls": [seed]})]
    links = [
        "https://soict.hust.edu.vn/tin-tuc/xyz",
        "https://hust.edu.vn/other",
        "https://www.soict.hust.edu.vn/z",
    ]
    asyncio.run(runtime.handle(_record(seed, links)))
    assert runtime.batcher.pending == ["https://soict.hust.edu.vn/tin-tuc/xyz"]


def test_initial_scope_is_adopted_only_once() -> None:
    state = MemoryMergerState()
    runtime = MergerRuntime(
        MergerConfig(database=Path("unused")),
        state,
        MemoryOutbox(),
        FakeServiceClient(),
        Scope(frozenset()),
        clock=lambda: 0.0,
    )
    runtime.adopt_initial_scope(frozenset({"old.hust.edu.vn"}))
    assert runtime.scope.allowed_hosts == {"old.hust.edu.vn"}
    runtime.adopt_initial_scope(frozenset({"other.hust.edu.vn"}))
    assert state.scope_hosts() == {"old.hust.edu.vn"}


def test_batch_cancelled_mid_send_goes_to_outbox() -> None:
    class _Hanging(FakeServiceClient):
        @override
        async def send(self, target: Target, payload: dict[str, Any]) -> SendResult:
            await asyncio.sleep(60)
            return "ok"

    outbox = MemoryOutbox()
    clock = [0.0]
    runtime = _runtime(_Hanging(), outbox, clock)
    runtime.batcher.add([f"https://{HOST}/1"], 0.0)
    clock[0] = 2.0

    async def scenario() -> None:
        task = asyncio.create_task(runtime.tick())
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await runtime.shutdown()

    asyncio.run(scenario())
    assert [(e.target, e.payload) for e in outbox.entries] == [
        ("mediator", {"urls": [f"https://{HOST}/1"]})
    ]
