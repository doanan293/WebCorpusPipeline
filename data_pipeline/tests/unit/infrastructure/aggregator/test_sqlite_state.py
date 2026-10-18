from __future__ import annotations

from pathlib import Path

from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState


def test_add_new_returns_only_unseen(tmp_path: Path) -> None:
    state = SqliteAggregatorState(tmp_path / "a.sqlite3")
    assert state.add_new(["a", "b", "a"]) == ["a", "b"]
    assert state.add_new(["b", "c"]) == ["c"]


def test_claim_keeps_first_owner_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "a.sqlite3"
    state = SqliteAggregatorState(path)
    assert state.claim("cid", "https://h/a") == "https://h/a"
    state.close()
    assert SqliteAggregatorState(path).claim("cid", "https://h/b") == "https://h/a"


def test_scope_hosts_persist_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "a.sqlite3"
    state = SqliteAggregatorState(path)
    assert state.scope_hosts() == frozenset()
    state.add_scope_hosts(["soict.hust.edu.vn", "ctsv.hust.edu.vn", "soict.hust.edu.vn"])
    state.close()
    assert SqliteAggregatorState(path).scope_hosts() == {"soict.hust.edu.vn", "ctsv.hust.edu.vn"}
