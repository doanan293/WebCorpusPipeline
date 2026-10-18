from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.parser.records import content_id_for
from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from scripts import migrate_aggregator


def test_aggregator_imports_all_urls_and_owners(
    crawl: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "aggregator.sqlite3"
    assert migrate_aggregator.main(["--crawl", str(crawl), "--database", str(database)]) == 0
    assert capsys.readouterr().out.split() == ["seen=12", "owners=2"]
    state = SqliteAggregatorState(database)
    assert state.add_new(["https://h.hust.edu.vn/a2", "https://google.com/", "https://new/"]) == [
        "https://new/"
    ]
    assert state.claim(content_id_for("same"), "https://other/") == "https://h.hust.edu.vn/a"
