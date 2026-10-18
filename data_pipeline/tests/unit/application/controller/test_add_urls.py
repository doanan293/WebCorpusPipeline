from __future__ import annotations

from data_pipeline.application.controller.add_urls import AddResult, add_urls
from tests.fakes.controller import FakeClock, FakeUrlQueue


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
