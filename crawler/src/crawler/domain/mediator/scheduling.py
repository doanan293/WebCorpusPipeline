from __future__ import annotations

import hashlib
from collections.abc import Mapping

from crawler.domain.mediator.models import MediatorConfig, QueuedUrl, UrlKind
from crawler.domain.shared.change import Change

DAY_SECONDS = 86400.0
MAX_RECRAWL_DAYS = 90.0
_SPREAD_STEPS = 10**6


def after_success(now: float, kind: UrlKind, recrawl_days: Mapping[UrlKind, float]) -> float | None:
    """When a URL sent now is due again; ``None`` when its kind is never recrawled."""
    days = recrawl_days[kind]
    return now + days * DAY_SECONDS if days > 0 else None


def after_failure(item: QueuedUrl, now: float, config: MediatorConfig) -> tuple[float | None, int]:
    """``(next_due_at, attempts)`` after a failed send.

    Due again now; at ``max_attempts`` one period later (a day when the kind is never recrawled).
    """
    attempts = item.attempts + 1
    if attempts < config.max_attempts:
        return now, attempts
    later = after_success(now, item.kind, config.recrawl_days)
    return (later if later is not None else now + DAY_SECONDS), 0


def spread(url: str, start: float, period_seconds: float) -> float:
    """A point in ``[start, start + period)`` fixed by the URL.

    Imported URLs get spread over their period instead of all falling due at once.
    """
    digest = int.from_bytes(hashlib.sha256(url.encode("utf-8")).digest()[:8])
    return start + (digest % _SPREAD_STEPS) / _SPREAD_STEPS * period_seconds


def adapt_interval(
    kind: UrlKind, current: float | None, change: Change, recrawl_days: Mapping[UrlKind, float]
) -> float:
    """The next recrawl period after a visit found ``change``.

    Hubs (front and listing pages) stay at their base period: new articles show up there. Other
    pages back off while unchanged (doubling, up to 90 days) and come closer when they change
    (halving, never below their base period).
    """
    base = recrawl_days[kind]
    if kind == "hub" or change == "new" or base <= 0:
        return base
    period = current or base
    if change == "modified":
        return max(period / 2, base)
    return min(period * 2, max(MAX_RECRAWL_DAYS, base))
