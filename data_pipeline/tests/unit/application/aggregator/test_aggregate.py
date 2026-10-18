from __future__ import annotations

import asyncio
from typing import Any

from data_pipeline.application.aggregator.aggregate import Scope, aggregate
from data_pipeline.application.aggregator.delivery import deliver, flush_outbox
from data_pipeline.application.aggregator.ports import Target
from data_pipeline.domain.aggregator.batching import UrlBatcher
from tests.fakes.aggregator import FakeServiceClient, MemoryAggregatorState, MemoryOutbox

SCOPE = Scope(allowed_hosts=frozenset({"hust.edu.vn"}))


def _record(url: str, content: str, links: list[tuple[str, str]]) -> dict[str, Any]:
    return {"url": url, "format": "html", "title": "T", "content": content, "out-links": links}


class _Run:
    def __init__(self) -> None:
        self.state = MemoryAggregatorState()
        self.batcher = UrlBatcher(max_size=500, max_wait=1.0)
        self.sent: list[tuple[Target, dict[str, Any]]] = []

    async def _deliver(self, target: Target, payload: dict[str, Any]) -> None:
        self.sent.append((target, payload))

    def add(self, record: dict[str, Any], now: float = 0.0) -> None:
        asyncio.run(
            aggregate(
                record,
                seen=self.state,
                owners=self.state,
                deliver=self._deliver,
                batcher=self.batcher,
                scope=SCOPE,
                now=now,
            )
        )

    def to(self, target: Target) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.sent if kind == target]


def test_record_goes_to_storage_and_new_links_to_controller() -> None:
    run = _Run()
    run.add(
        _record(
            "https://hust.edu.vn/",
            "home",
            [("https://hust.edu.vn/a", "A"), ("https://x.com/", "X")],
        )
    )
    assert [payload["url"] for payload in run.to("storage")] == ["https://hust.edu.vn/"]
    assert run.batcher.due(1.0) == ["https://hust.edu.vn/a"]


def test_known_links_not_resent() -> None:
    run = _Run()
    record = _record("https://hust.edu.vn/", "home", [("https://hust.edu.vn/a", "A")])
    run.add(record)
    run.add(record)
    assert run.batcher.due(1.0) == ["https://hust.edu.vn/a"]


def test_page_url_itself_counts_as_known() -> None:
    run = _Run()
    run.add(_record("https://hust.edu.vn/a", "a", []))
    run.add(_record("https://hust.edu.vn/", "home", [("https://hust.edu.vn/a", "A")]))
    assert run.batcher.due(1.0) is None


def test_duplicate_content_sends_stub() -> None:
    run = _Run()
    run.add(_record("https://hust.edu.vn/a", "same", []))
    run.add(_record("https://hust.edu.vn/b", "same", [("https://hust.edu.vn/c", "C")]))
    stored = run.to("storage")
    assert stored[1]["content"] == ""
    assert stored[1]["duplicate_of"] == "https://hust.edu.vn/a"
    assert stored[1]["out-links"] == [("https://hust.edu.vn/c", "C")]


def test_empty_pages_are_not_duplicates() -> None:
    run = _Run()
    run.add(_record("https://hust.edu.vn/a", " ", []))
    run.add(_record("https://hust.edu.vn/b", "", []))
    assert ["duplicate_of" in payload for payload in run.to("storage")] == [False, False]


def test_recrawled_page_updates_storage() -> None:
    run = _Run()
    run.add(_record("https://hust.edu.vn/a", "v1", []))
    run.add(_record("https://hust.edu.vn/a", "v1", []))
    assert len(run.to("storage")) == 2
    assert "duplicate_of" not in run.to("storage")[1]


def test_full_batch_goes_to_controller_at_once() -> None:
    run = _Run()
    links = [(f"https://hust.edu.vn/{i}", "") for i in range(500)]
    run.add(_record("https://hust.edu.vn/", "home", links))
    (batch,) = run.to("controller")
    assert len(batch["urls"]) == 500


def test_failed_delivery_goes_to_outbox_and_flushes() -> None:
    outbox = MemoryOutbox()
    asyncio.run(deliver("storage", {"url": "u"}, client=FakeServiceClient(ok=False), outbox=outbox))
    assert len(outbox.pending()) == 1
    client = FakeServiceClient(ok=True)
    assert asyncio.run(flush_outbox(client, outbox)) == 1
    assert outbox.pending() == []
    assert client.sent == [("storage", {"url": "u"})]
