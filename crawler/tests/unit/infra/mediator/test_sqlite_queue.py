from __future__ import annotations

import sqlite3
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


def test_old_queue_file_gains_the_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "q.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE urls (url TEXT PRIMARY KEY, host TEXT NOT NULL, kind TEXT NOT NULL,"
        " next_due_at REAL, last_sent_at REAL, attempts INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO urls VALUES ('https://h/a', 'h', 'page', 5.0, NULL, 0);"
    )
    old.commit()
    old.close()
    queue = SqliteUrlQueue(path)
    row = queue.get("https://h/a")
    assert row is not None and (row.next_due_at, row.etag, row.interval_days) == (5.0, None, None)
    queue.reschedule(
        "https://h/a", next_due_at=9.0, interval_days=14.0, etag='"e"', last_modified="Mon"
    )
    queue.close()
    again = SqliteUrlQueue(path).get("https://h/a")
    assert again is not None
    assert (again.next_due_at, again.interval_days, again.etag, again.last_modified) == (
        9.0,
        14.0,
        '"e"',
        "Mon",
    )
    assert SqliteUrlQueue(path).get("https://h/none") is None


def test_make_due_requeues_known_url_without_validators(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    queue.add([("https://a/1", "a", "page")], 1.0)
    queue.mark_sent("https://a/1", 2.0, 50.0)
    queue.reschedule(
        "https://a/1", next_due_at=50.0, interval_days=7.0, etag="e", last_modified="m"
    )
    assert queue.make_due(["https://a/1", "https://a/unknown"], 3.0) == 1
    row = queue.get("https://a/1")
    assert row is not None
    # Without its validators the download answers in full, so the page's links can be followed.
    assert (row.next_due_at, row.attempts, row.etag, row.last_modified) == (3.0, 0, None, None)
