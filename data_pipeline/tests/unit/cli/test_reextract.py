from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.application.parser import reextract as reextract_module
from data_pipeline.cli.main import main
from data_pipeline.domain.shared.config import ProjectPaths


def test_reextract_reads_the_crawl_and_posts_to_the_aggregator(
    project: ProjectPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[tuple[object, object]] = []

    async def fake(pages: object, aggregator: object) -> int:
        seen.append((getattr(pages, "crawl", None), getattr(aggregator, "url", None)))
        return 7

    monkeypatch.setattr(reextract_module, "reextract", fake)
    code = main(
        ["reextract", "--crawl", str(tmp_path / "crawl"), "--aggregator", "http://agg:3300"]
    )
    assert code == 0
    assert seen == [(tmp_path / "crawl", "http://agg:3300/add")]
    assert "7" in capsys.readouterr().out
