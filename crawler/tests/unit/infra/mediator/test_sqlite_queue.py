from __future__ import annotations

from pathlib import Path

from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue


def test_sqlite_queue_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "q.sqlite3"
    queue = SqliteUrlQueue(path)
    assert queue.add([("https://a/1", "a", "page"), ("https://a/1", "a", "page")], 1.0) == 1
    queue.mark_sent("https://a/1", 2.0, 50.0)
    queue.close()
    reopened = SqliteUrlQueue(path)
    assert reopened.counts(10.0).total == 1
    row = reopened.next_due(60.0, ())
    assert row is not None
    assert (row.last_sent_at, row.next_due_at, row.kind) == (2.0, 50.0, "page")


def test_sqlite_next_due_skips_excluded_hosts(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    queue.add(
        [("https://a/1", "a", "page"), ("https://a/2", "a", "page"), ("https://b/1", "b", "hub")],
        0.0,
    )
    row = queue.next_due(10.5, {"a"})
    assert row is not None and row.host == "b"
    assert queue.next_due(10.5, {"a", "b"}) is None
    row = queue.next_due(11.0, ())
    assert row is not None and row.url in {"https://a/1", "https://a/2", "https://b/1"}


def test_sqlite_never_due_and_failures(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    queue.add([("https://a/1", "a", "page")], 0.0)
    queue.mark_failed("https://a/1", 3, 5.0)
    assert queue.next_due(4.0, ()) is None
    row = queue.next_due(5.0, ())
    assert row is not None and row.attempts == 3
    queue.mark_sent("https://a/1", 6.0, None)
    assert queue.next_due(1e12, ()) is None
    assert queue.counts(1e12).due == 0


def test_has_due(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    assert not queue.has_due(10.0)
    queue.add([("https://a/1", "a", "page")], 5.0)
    assert not queue.has_due(4.0)
    assert queue.has_due(5.0)
