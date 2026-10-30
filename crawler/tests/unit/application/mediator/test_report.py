from __future__ import annotations

from crawler.application.mediator.report import report_outcomes
from crawler.domain.mediator.models import DEFAULT_RECRAWL_DAYS
from tests.fakes.mediator import FakeClock, FakeUrlQueue

DAY = 86400.0
DAYS = DEFAULT_RECRAWL_DAYS


def test_reports_adapt_schedule_and_keep_validators() -> None:
    queue = FakeUrlQueue()
    queue.add([("https://h/a", "h", "page"), ("https://h/", "h", "hub")], 0.0)
    reports = [
        {"url": "https://h/a", "change": "unchanged", "etag": '"e"', "last_modified": "Mon"},
        {"url": "https://h/", "change": "unchanged", "etag": None, "last_modified": None},
        {"url": "https://h/unknown", "change": "new", "etag": None, "last_modified": None},
    ]
    assert report_outcomes(queue, reports, DAYS, FakeClock(100.0)) == 2
    page, hub = queue.get("https://h/a"), queue.get("https://h/")
    assert page is not None and hub is not None
    assert (page.interval_days, page.next_due_at, page.etag, page.last_modified) == (
        14.0,
        100.0 + 14 * DAY,
        '"e"',
        "Mon",
    )
    assert (hub.interval_days, hub.next_due_at) == (1.0, 100.0 + DAY)
    report_outcomes(
        queue,
        [{"url": "https://h/a", "change": "modified", "etag": None, "last_modified": None}],
        DAYS,
        FakeClock(200.0),
    )
    page = queue.get("https://h/a")
    assert page is not None and (page.interval_days, page.etag) == (7.0, None)


def test_no_recrawl_period_takes_the_url_off_the_schedule() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(100.0)
    queue.add([("https://hust.edu.vn/a", "hust.edu.vn", "page")], 0.0)
    report = {"url": "https://hust.edu.vn/a", "change": "new", "etag": None, "last_modified": None}
    report_outcomes(queue, [report], {"hub": 0, "page": 0, "document": 0}, clock)
    assert queue.rows["https://hust.edu.vn/a"].next_due_at is None
