from __future__ import annotations

from pathlib import Path

from crawler.domain.mediator.models import MediatorConfig, QueuedUrl, UrlKind
from crawler.domain.mediator.scheduling import (
    MAX_RECRAWL_DAYS,
    adapt_interval,
    after_failure,
    after_success,
    spread,
)

DAY = 86400.0
PERIODS: dict[UrlKind, float] = {"hub": 1.0, "page": 7.0, "document": 30.0}


def _config(**overrides: object) -> MediatorConfig:
    return MediatorConfig(downloaders=("http://a",), database=Path("q.sqlite3"), **overrides)  # type: ignore[arg-type]


def _item(attempts: int) -> QueuedUrl:
    return QueuedUrl("https://x/a", "x", "page", 0.0, None, attempts)


def test_default_config_matches_spec() -> None:
    config = _config()
    assert (config.port, config.host_delay_seconds, config.max_attempts) == (5200, 1.0, 5)
    assert dict(config.recrawl_days) == PERIODS


def test_after_success_uses_kind_period() -> None:
    assert after_success(1000.0, "page", PERIODS) == 1000.0 + 7 * DAY


def test_after_success_zero_period_disables() -> None:
    assert after_success(0.0, "hub", {**PERIODS, "hub": 0.0}) is None


def test_after_failure_keeps_due_until_max_attempts() -> None:
    assert after_failure(_item(2), 50.0, _config()) == (50.0, 3)


def test_after_failure_backs_off_one_period_at_max() -> None:
    assert after_failure(_item(4), 50.0, _config()) == (50.0 + 7 * DAY, 0)


def test_after_failure_at_max_with_zero_period_waits_a_day() -> None:
    config = _config(recrawl_days={**PERIODS, "page": 0.0})
    assert after_failure(_item(4), 50.0, config) == (50.0 + DAY, 0)


def test_spread_is_stable_and_inside_period() -> None:
    first = spread("https://x/1", 100.0, DAY)
    assert first == spread("https://x/1", 100.0, DAY)
    assert 100.0 <= first < 100.0 + DAY
    assert first != spread("https://x/2", 100.0, DAY)


def test_adapt_interval() -> None:
    days: dict[UrlKind, float] = {"hub": 1.0, "page": 7.0, "document": 30.0}
    assert adapt_interval("hub", 1.0, "unchanged", days) == 1.0
    assert adapt_interval("hub", None, "modified", days) == 1.0
    assert adapt_interval("page", None, "unchanged", days) == 14.0
    assert adapt_interval("page", 14.0, "not_modified", days) == 28.0
    assert adapt_interval("page", 80.0, "unchanged", days) == MAX_RECRAWL_DAYS == 90.0
    assert adapt_interval("page", 14.0, "modified", days) == 7.0
    assert adapt_interval("page", 8.0, "modified", days) == 7.0
    assert adapt_interval("document", 120.0, "new", days) == 30.0
