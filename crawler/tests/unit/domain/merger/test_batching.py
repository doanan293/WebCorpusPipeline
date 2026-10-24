from __future__ import annotations

from crawler.domain.merger.batching import UrlBatcher


def test_batcher_flushes_at_size() -> None:
    batcher = UrlBatcher(max_size=500, max_wait=1.0)
    full = batcher.add([f"https://h/{i}" for i in range(501)], now=0.0)
    assert [len(batch) for batch in full] == [500]
    assert batcher.due(0.5) is None
    assert batcher.due(1.0) == ["https://h/500"]


def test_batcher_flushes_after_wait() -> None:
    batcher = UrlBatcher(max_size=500, max_wait=1.0)
    assert batcher.add(["a", "b"], now=0.0) == []
    batcher.add(["c"], now=0.6)
    assert batcher.due(0.5) is None
    assert batcher.due(1.0) == ["a", "b", "c"]
    assert batcher.due(5.0) is None
