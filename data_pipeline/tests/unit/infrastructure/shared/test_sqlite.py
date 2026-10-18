from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue
from data_pipeline.infrastructure.shared.sqlite import connect, transaction


def _statements(db: sqlite3.Connection) -> list[str]:
    seen: list[str] = []
    db.set_trace_callback(seen.append)
    return seen


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    db = connect(tmp_path / "t.sqlite3")
    db.execute("CREATE TABLE t (x INTEGER PRIMARY KEY)")
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute("INSERT INTO t VALUES (1)")
        db.execute("INSERT INTO t VALUES (1)")
    assert db.execute("SELECT COUNT(*) FROM t").fetchone() == (0,)


def test_queue_add_is_one_transaction(tmp_path: Path) -> None:
    queue = SqliteUrlQueue(tmp_path / "q.sqlite3")
    seen = _statements(queue.db)
    queue.add([(f"https://h/{i}", "h", "page") for i in range(3)], 0.0)
    assert [s for s in seen if s in {"BEGIN", "COMMIT"}] == ["BEGIN", "COMMIT"]


def test_aggregator_add_new_is_one_transaction(tmp_path: Path) -> None:
    state = SqliteAggregatorState(tmp_path / "a.sqlite3")
    seen = _statements(state.db)
    assert state.add_new(["a", "b", "c"]) == ["a", "b", "c"]
    assert [s for s in seen if s in {"BEGIN", "COMMIT"}] == ["BEGIN", "COMMIT"]
