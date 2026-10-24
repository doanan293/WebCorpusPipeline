from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from services.api.storage import create_app
from tests.fakes.storage import FakeCorpusStore

RECORD = {
    "url": "https://h/a",
    "format": "html",
    "title": "A",
    "content": "c",
    "out-links": [["https://h/b", "B"]],
}


def _post(store: FakeCorpusStore, body: object) -> tuple[int, object]:
    async def scenario() -> tuple[int, object]:
        async with TestClient(TestServer(create_app(store, lambda: "now"))) as client:
            response = await client.post("/add", json=body)
            return response.status, await response.json()

    return asyncio.run(scenario())


def test_add_stores_record() -> None:
    store = FakeCorpusStore()
    assert _post(store, RECORD) == (200, {"status": "ok"})
    assert store.collections["html"]["https://h/a"]["title"] == "A"
    assert store.graph == [{"src": "https://h/a", "dest": "https://h/b", "text": "B"}]


def test_add_invalid_record_is_400() -> None:
    store = FakeCorpusStore()
    status, _ = _post(store, {"url": "https://h/a"})
    assert status == 400
    assert store.collections["html"] == {}


def test_add_503_when_store_down() -> None:
    store = FakeCorpusStore()
    store.down = True
    assert _post(store, RECORD) == (503, {"error": "mongo unavailable"})


def test_add_empty_object_is_ok() -> None:
    store = FakeCorpusStore()
    assert _post(store, {}) == (200, {"status": "ok"})
    assert store.collections["html"] == {}


def test_too_large_document_is_rejected_with_422() -> None:
    store = FakeCorpusStore()
    store.reject = True
    status, body = _post(store, RECORD)
    assert status == 422
    assert isinstance(body, dict) and "error" in body
