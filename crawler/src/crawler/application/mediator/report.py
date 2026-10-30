from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from crawler.application.mediator.ports import Clock, UrlQueue
from crawler.domain.mediator.models import UrlKind
from crawler.domain.mediator.scheduling import DAY_SECONDS, adapt_interval
from crawler.domain.shared.change import Change

CHANGES: frozenset[str] = frozenset({"new", "modified", "unchanged", "not_modified"})


def valid_report(report: object) -> bool:
    if not isinstance(report, Mapping):
        return False
    return (
        isinstance(report.get("url"), str)
        and report.get("change") in CHANGES
        and all(
            report.get(key) is None or isinstance(report.get(key), str)
            for key in ("etag", "last_modified")
        )
    )


def report_outcomes(
    queue: UrlQueue,
    reports: Sequence[Mapping[str, Any]],
    recrawl_days: Mapping[UrlKind, float],
    clock: Clock,
) -> int:
    """Reschedule each reported URL by what its visit found; return how many were queued."""
    now = clock.now()
    updated = 0
    for report in reports:
        item = queue.get(report["url"])
        if item is None:
            continue
        change: Change = report["change"]
        interval = adapt_interval(item.kind, item.interval_days, change, recrawl_days)
        etag, last_modified = report.get("etag"), report.get("last_modified")
        if change == "not_modified":
            # A 304 carries no new validators; the stored ones still describe the page.
            etag, last_modified = etag or item.etag, last_modified or item.last_modified
        queue.reschedule(
            report["url"],
            # No period (0 = no recrawl): off the schedule, or it would be due again at once.
            next_due_at=now + interval * DAY_SECONDS if interval > 0 else None,
            interval_days=interval,
            etag=etag,
            last_modified=last_modified,
        )
        updated += 1
    return updated
