from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from data_pipeline.domain.mediator.models import MediatorConfig
from data_pipeline.domain.mediator.throttle import HostThrottle


def _config(**overrides: Any) -> MediatorConfig:
    base = MediatorConfig(
        downloaders=("n",), database=Path("unused"), host_delay_seconds=0.0, per_host_concurrency=2
    )
    return replace(base, **overrides)


def test_concurrency_limit_blocks_host() -> None:
    throttle = HostThrottle(_config(autothrottle=False))
    throttle.started("a", 0.0)
    assert "a" not in throttle.blocked(0.0)
    throttle.started("a", 0.0)
    assert "a" in throttle.blocked(0.0)
    throttle.finished("a", 1.0)
    assert "a" not in throttle.blocked(1.0)


def test_delay_starts_at_the_start_delay() -> None:
    throttle = HostThrottle(_config())
    assert throttle.delay("a") == 0.25
    throttle.started("a", 10.0)
    assert "a" in throttle.blocked(10.2)
    assert "a" not in throttle.blocked(10.25)


def test_slow_responses_raise_delay() -> None:
    throttle = HostThrottle(_config())
    for _ in range(10):
        throttle.started("a", 0.0)
        throttle.finished("a", 8.0)
    # latency 8 s over 4 target requests in flight: 2 s between sends, capped at 5 s.
    assert 1.9 <= throttle.delay("a") <= 2.0


def test_delay_never_exceeds_the_maximum() -> None:
    throttle = HostThrottle(_config())
    for _ in range(20):
        throttle.started("a", 0.0)
        throttle.finished("a", 60.0)
    assert throttle.delay("a") == 5.0


def test_fast_responses_lower_delay_to_minimum() -> None:
    throttle = HostThrottle(_config(host_delay_seconds=0.1))
    for _ in range(20):
        throttle.started("a", 0.0)
        throttle.finished("a", 0.01)
    assert throttle.delay("a") == 0.1


def test_slow_host_does_not_block_other_hosts() -> None:
    throttle = HostThrottle(_config())
    for _ in range(10):
        throttle.started("slow", 0.0)
        throttle.finished("slow", 60.0)
    throttle.started("slow", 100.0)
    throttle.started("fast", 100.0)
    throttle.finished("fast", 0.1)
    assert throttle.blocked(101.0) == frozenset({"slow"})


def test_disabled_throttle_uses_fixed_delay() -> None:
    throttle = HostThrottle(_config(autothrottle=False, host_delay_seconds=1.0))
    throttle.started("a", 0.0)
    throttle.finished("a", 30.0)
    assert throttle.delay("a") == 1.0


def test_unknown_latency_keeps_delay() -> None:
    throttle = HostThrottle(_config())
    throttle.started("a", 0.0)
    throttle.finished("a", None)
    assert throttle.delay("a") == 0.25
    assert "a" not in throttle.blocked(1.0)


def test_next_unblock_is_the_earliest_wait_ending() -> None:
    throttle = HostThrottle(_config(autothrottle=False, host_delay_seconds=1.0))
    assert throttle.next_unblock(0.0) is None
    throttle.started("a", 10.0)
    throttle.finished("a", 0.1)
    throttle.started("b", 10.5)
    throttle.finished("b", 0.1)
    assert throttle.next_unblock(10.6) == 11.0
    assert throttle.next_unblock(11.2) == 11.5
    assert throttle.next_unblock(12.0) is None
