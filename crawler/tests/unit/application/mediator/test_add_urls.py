from __future__ import annotations

from crawler.application.mediator.add_urls import AddResult, add_urls, cancel_urls
from tests.fakes.mediator import FakeClock, FakeUrlQueue


def test_add_urls_counts() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(0.0)
    result = add_urls(
        queue, ["https://hust.edu.vn/a", "https://hust.edu.vn/a#x", "mailto:x"], clock
    )
    assert result == AddResult(accepted=1, duplicates=1, rejected=1)
    assert queue.rows["https://hust.edu.vn/a"].kind == "page"
    assert queue.rows["https://hust.edu.vn/a"].host == "hust.edu.vn"


def test_add_known_url_keeps_schedule() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(0.0)
    add_urls(queue, ["https://hust.edu.vn/a"], clock)
    queue.mark_sent("https://hust.edu.vn/a", 0.0, 99.0)
    clock.t = 10.0
    assert add_urls(queue, ["https://hust.edu.vn/a"], clock) == AddResult(0, 1, 0)
    assert queue.rows["https://hust.edu.vn/a"].next_due_at == 99.0


def test_add_accepts_url_on_non_default_port() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(0.0)
    assert add_urls(queue, ["http://127.0.0.1:5000/a"], clock) == AddResult(1, 0, 0)
    assert queue.rows["http://127.0.0.1:5000/a"].host == "127.0.0.1"


def test_force_makes_known_url_due_again() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(0.0)
    add_urls(queue, ["https://hust.edu.vn/a"], clock)
    queue.mark_sent("https://hust.edu.vn/a", 0.0, 99.0)
    clock.t = 10.0
    assert add_urls(queue, ["https://hust.edu.vn/a"], clock, force=True) == AddResult(0, 1, 0)
    assert queue.rows["https://hust.edu.vn/a"].next_due_at == 0.0


def test_forced_url_goes_ahead_of_the_backlog() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(5.0)
    add_urls(queue, ["https://hust.edu.vn/old"], clock)
    clock.t = 10.0
    add_urls(queue, ["https://hust.edu.vn/new"], clock, force=True)
    row = queue.next_due(10.0, ())
    assert row is not None
    assert row.url == "https://hust.edu.vn/new"


def test_cancel_takes_urls_off_the_schedule() -> None:
    queue, clock = FakeUrlQueue(), FakeClock(0.0)
    add_urls(queue, ["https://hust.edu.vn/a", "https://hust.edu.vn/b"], clock)
    assert cancel_urls(queue, ["https://hust.edu.vn/a#x", "mailto:x"]) == 1
    assert queue.rows["https://hust.edu.vn/a"].next_due_at is None
    assert queue.rows["https://hust.edu.vn/b"].next_due_at == 0.0
    add_urls(queue, ["https://hust.edu.vn/a"], clock, force=True)
    assert queue.rows["https://hust.edu.vn/a"].next_due_at is not None
