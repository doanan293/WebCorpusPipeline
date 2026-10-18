from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import StateLockedError
from data_pipeline.infrastructure.shared.lease import SqliteLease
from tests.fakes.controller import FakeClock


def test_second_holder_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    clock = FakeClock(1000.0)
    first = SqliteLease(path, "dich-vu controller@a:1", clock)
    first.acquire()
    second = SqliteLease(path, "data-pipeline crawl@a:2", clock)

    with pytest.raises(StateLockedError, match="dich-vu controller@a:1"):
        second.acquire()


def test_release_lets_next_holder_in(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    clock = FakeClock(1000.0)
    first = SqliteLease(path, "a", clock)
    first.acquire()
    first.release()

    SqliteLease(path, "b", clock).acquire()


def test_stale_lease_is_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    clock = FakeClock(1000.0)
    SqliteLease(path, "crashed", clock, ttl=60).acquire()
    clock.t += 61

    SqliteLease(path, "b", clock, ttl=60).acquire()


def test_renewed_lease_stays_held(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    clock = FakeClock(1000.0)
    first = SqliteLease(path, "a", clock, ttl=60)
    first.acquire()
    clock.t += 50
    first.renew()
    clock.t += 50

    with pytest.raises(StateLockedError):
        SqliteLease(path, "b", clock, ttl=60).acquire()


def test_same_owner_may_acquire_again(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    clock = FakeClock(1000.0)
    SqliteLease(path, "a", clock).acquire()

    SqliteLease(path, "a", clock).acquire()


def test_renewal_survives_a_failed_renew(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease = SqliteLease(tmp_path / "state.sqlite3", "a", FakeClock(1000.0), renew_every=0.01)
    calls: list[int] = []

    def flaky_renew() -> None:
        calls.append(1)
        if len(calls) == 1:
            locked = "database is locked"
            raise sqlite3.OperationalError(locked)

    monkeypatch.setattr(lease, "renew", flaky_renew)
    with lease.held():
        deadline = time.monotonic() + 2
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(calls) >= 3
