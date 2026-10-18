from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.application.aggregator.aggregate import Scope
from data_pipeline.application.aggregator.delivery import deliver, flush_outbox
from data_pipeline.application.aggregator.ports import SendResult, Target
from data_pipeline.application.aggregator.runtime import AggregatorRuntime
from data_pipeline.domain.aggregator.config import AggregatorConfig
from tests.fakes.aggregator import FakeServiceClient, MemoryAggregatorState, MemoryOutbox

HOST = "a.hust.edu.vn"


def _record(url: str, links: list[str]) -> dict[str, Any]:
    return {
        "url": url,
        "format": "html",
        "title": "t",
        "content": f"text of {url}",
        "out-links": [[link, "x"] for link in links],
    }


def _runtime(
    client: FakeServiceClient, outbox: MemoryOutbox, clock: list[float]
) -> AggregatorRuntime:
    return AggregatorRuntime(
        AggregatorConfig(batch_size=500, batch_seconds=1.0, database=Path("unused")),
        MemoryAggregatorState(),
        outbox,
        client,
        Scope(frozenset({HOST}), ()),
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


def test_retry_on_storage_does_not_block_controller_entries() -> None:
    outbox = MemoryOutbox()
    outbox.append("storage", {"url": "u1"})
    outbox.append("controller", {"urls": ["u2"]})
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
        ("controller", {"urls": [f"https://{HOST}/new"]})
    ]
    assert runtime.batcher.pending == []


def test_seed_goes_to_controller() -> None:
    client = FakeServiceClient()
    runtime = _runtime(client, MemoryOutbox(), [0.0])
    asyncio.run(runtime.seed([f"https://{HOST}/"]))
    assert client.sent == [("controller", {"urls": [f"https://{HOST}/"]})]
