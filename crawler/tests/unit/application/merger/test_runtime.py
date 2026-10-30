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


JOB_HOST = "b.hust.edu.vn"
JOB_ROOT = f"https://{JOB_HOST}/"


def _job_runtime(client: FakeServiceClient) -> MergerRuntime:
    return MergerRuntime(
        MergerConfig(batch_size=500, batch_seconds=1.0, database=Path("unused")),
        MemoryMergerState(),
        MemoryOutbox(),
        client,
        Scope(frozenset()),
        clock=lambda: 0.0,
    )


def _sent_to_mediator(client: FakeServiceClient) -> list[dict[str, Any]]:
    return [payload for target, payload in client.sent if target == "mediator"]


def test_start_crawl_forces_the_url_without_widening_scope() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)
    job = asyncio.run(runtime.start_crawl(JOB_ROOT, 10))
    assert not isinstance(job, str)
    assert job.host == JOB_HOST
    assert _sent_to_mediator(client) == [{"urls": [JOB_ROOT], "force": True}]
    assert runtime.scope.allowed_hosts == frozenset()


def test_job_spreads_within_its_host_only() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, 10)
        await runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}p1", "https://c.hust.edu.vn/z"]))

    asyncio.run(scenario())
    assert _sent_to_mediator(client)[1:] == [{"urls": [f"{JOB_ROOT}p1"], "force": True}]
    assert runtime.scope.allowed_hosts == frozenset()


def test_job_stops_at_max_pages() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, 2)
        await runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}p1", f"{JOB_ROOT}p2"]))

    asyncio.run(scenario())
    assert _sent_to_mediator(client)[1:] == [{"urls": [f"{JOB_ROOT}p1"], "force": True}]


def test_job_matches_redirected_record() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)

    async def scenario() -> dict[str, Any]:
        job = await runtime.start_crawl(f"http://{JOB_HOST}", None)
        assert not isinstance(job, str)
        record = {**_record(JOB_ROOT, []), "requested_url": job.url}
        await runtime.handle(record)
        return runtime.crawl_status(job)

    status = asyncio.run(scenario())
    assert status["saved"] == 1
    assert status["urls"] == [JOB_ROOT]
    assert status["done"] is True


def test_unchanged_page_still_spreads_for_job() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)
    page = {**_record(JOB_ROOT, [f"{JOB_ROOT}p1"]), "fingerprint": "same"}

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, None)
        await runtime.handle(page)
        await runtime.start_crawl(JOB_ROOT, None)
        await runtime.handle(page)

    asyncio.run(scenario())
    assert _sent_to_mediator(client)[-1] == {"urls": [f"{JOB_ROOT}p1"], "force": True}
    assert sum(1 for p in _sent_to_mediator(client) if p["urls"] == [f"{JOB_ROOT}p1"]) == 2


def test_duplicate_page_is_a_dead_end_for_job() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)
    first = {**_record(JOB_ROOT, [f"{JOB_ROOT}?page=1"]), "content": "same text"}
    echo = {**_record(f"{JOB_ROOT}?page=1", [f"{JOB_ROOT}?page=2"]), "content": "same text"}

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, None)
        await runtime.handle(first)
        await runtime.handle(echo)

    asyncio.run(scenario())
    assert all(f"{JOB_ROOT}?page=2" not in p["urls"] for p in _sent_to_mediator(client))


def test_record_outside_any_job_changes_nothing() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)
    asyncio.run(runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}p1"])))
    assert _sent_to_mediator(client) == []


@pytest.mark.parametrize(
    "url", ["ftp://b.hust.edu.vn/x", "not a url", "", "https://a b/", " https://b.hust.edu.vn/"]
)
def test_start_crawl_rejects_bad_url(url: str) -> None:
    client = FakeServiceClient()
    assert isinstance(asyncio.run(_job_runtime(client).start_crawl(url, None)), str)
    assert client.sent == []


def test_tick_retries_a_job_url_that_never_came_back() -> None:
    client = FakeServiceClient()
    clock = [0.0]
    runtime = MergerRuntime(
        MergerConfig(batch_size=500, batch_seconds=1.0, database=Path("unused")),
        MemoryMergerState(),
        MemoryOutbox(),
        client,
        Scope(frozenset()),
        clock=lambda: clock[0],
    )

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, None)
        await runtime.tick()
        clock[0] = 15.0
        await runtime.tick()

    asyncio.run(scenario())
    assert _sent_to_mediator(client) == [{"urls": [JOB_ROOT], "force": True}] * 2


def test_stop_crawl_cancels_urls_still_out() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)

    async def scenario() -> None:
        job = await runtime.start_crawl(JOB_ROOT, None)
        assert not isinstance(job, str)
        await runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}p1"]))
        assert await runtime.stop_crawl(job.id) is job
        assert await runtime.stop_crawl("nope") is None

    asyncio.run(scenario())
    assert _sent_to_mediator(client)[-1] == {"urls": [f"{JOB_ROOT}p1"], "cancel": True}


def test_start_link_redirecting_to_another_host_moves_the_job() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)
    www = "https://www.b.hust.edu.vn/"

    async def scenario() -> str:
        job = await runtime.start_crawl(JOB_ROOT, 10)
        assert not isinstance(job, str)
        await runtime.handle(
            {**_record(www, [f"{www}p1", f"{JOB_ROOT}old"]), "requested_url": job.url}
        )
        return job.host

    assert asyncio.run(scenario()) == "www.b.hust.edu.vn"
    assert _sent_to_mediator(client)[-1] == {"urls": [f"{www}p1"], "force": True}
    assert runtime.scope.allowed_hosts == frozenset()


def test_status_reports_answered_urls() -> None:
    client = FakeServiceClient()
    runtime = _job_runtime(client)

    async def scenario() -> dict[str, Any]:
        job = await runtime.start_crawl(JOB_ROOT, None)
        assert not isinstance(job, str)
        await runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}p1"]))
        return runtime.crawl_status(job)

    status = asyncio.run(scenario())
    assert (status["queued"], status["answered"], status["saved"]) == (2, 1, 1)


def test_failed_urls_give_their_place_to_links_waiting_over_budget() -> None:
    client = FakeServiceClient()
    clock = [0.0]
    runtime = MergerRuntime(
        MergerConfig(batch_size=500, batch_seconds=1.0, database=Path("unused")),
        MemoryMergerState(),
        MemoryOutbox(),
        client,
        Scope(frozenset()),
        clock=lambda: clock[0],
    )

    async def scenario() -> None:
        await runtime.start_crawl(JOB_ROOT, 2)
        await runtime.handle(_record(JOB_ROOT, [f"{JOB_ROOT}bad", f"{JOB_ROOT}good"]))
        for now in (15.0, 30.0, 45.0):
            clock[0] = now
            await runtime.tick()

    asyncio.run(scenario())
    assert _sent_to_mediator(client)[-1] == {"urls": [f"{JOB_ROOT}good"], "force": True}
