from __future__ import annotations

import time
from pathlib import Path

import pytest

from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue
from scripts import migrate_controller
from tests.scripts.conftest import FINISHED_AT

DAY = 86400.0


def _rows(database: Path) -> dict[str, tuple[str, float | None, float | None]]:
    queue = SqliteUrlQueue(database)
    rows = queue.db.execute("SELECT url, kind, next_due_at, last_sent_at FROM urls").fetchall()
    return {url: (kind, due, sent) for url, kind, due, sent in rows}


def test_controller_counts_and_due_now(
    crawl: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "controller.sqlite3"
    before = time.time()
    assert migrate_controller.main(["--crawl", str(crawl), "--database", str(database)]) == 0
    out = dict(line.split("=") for line in capsys.readouterr().out.split())
    assert out == {
        "extracted": "3",
        "scheduled": "1",
        "failed": "1",
        "documents": "1",
        "ignored": "5",
        "imported": "6",
    }
    rows = _rows(database)
    assert set(rows) == {
        "https://h.hust.edu.vn/",
        "https://h.hust.edu.vn/a",
        "https://h.hust.edu.vn/dup",
        "https://h.hust.edu.vn/next",
        "https://h.hust.edu.vn/broken",
        "https://h.hust.edu.vn/qd.pdf",
    }
    assert (
        rows["https://h.hust.edu.vn/next"][1] is not None
        and rows["https://h.hust.edu.vn/next"][1] <= time.time()
    )
    assert rows["https://h.hust.edu.vn/next"][1] >= before - 1
    # Pages already fetched are not crawled again now: the first check comes after a full period.
    after = time.time()
    home = rows["https://h.hust.edu.vn/"]
    fetched = 1_792_120_471.0  # 2026-10-16T03:14:31+00:00
    assert home[0] == "hub" and home[2] == fetched
    assert before + DAY <= (home[1] or 0) < after + 2 * DAY
    page = rows["https://h.hust.edu.vn/a"]
    assert page[2] == FINISHED_AT
    assert before + 7 * DAY <= (page[1] or 0) < after + 14 * DAY
    document = rows["https://h.hust.edu.vn/qd.pdf"]
    assert before + 30 * DAY <= (document[1] or 0) < after + 60 * DAY
    assert rows["https://h.hust.edu.vn/qd.pdf"][0] == "document"


def test_extracted_due_times_are_spread(tmp_path: Path) -> None:
    due = [migrate_controller.spread_due(f"https://h/{i}", 0.0, 7 * DAY) for i in range(200)]
    assert len(set(due)) == 200
    assert max(due) - min(due) > 0.5 * 7 * DAY


def test_controller_rerun_is_noop(crawl: Path, tmp_path: Path) -> None:
    database = tmp_path / "controller.sqlite3"
    args = ["--crawl", str(crawl), "--database", str(database)]
    migrate_controller.main(args)
    first = _rows(database)
    migrate_controller.main(args)
    assert _rows(database) == first
