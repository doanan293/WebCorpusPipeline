from __future__ import annotations

from pathlib import Path

from data_pipeline.enrich.models import ParseResult
from data_pipeline.enrich.state import WorkState


def test_requeue_cloud_parses_forgets_only_results_that_used_the_cloud(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "c1") as state:
        local_only = ParseResult("a", [], "extracted", [], {"local": "pymupdf"})
        with_cloud = ParseResult("b", [], "extracted", [], {"local": "pymupdf", "llamaparse": "x"})
        state.mark_parse("https://a.test/local.pdf", local_only)
        state.mark_parse("https://a.test/cloud.pdf", with_cloud, tmp_path / "cache" / "k.json")

        assert state.requeue_cloud_parses() == 1
        remaining = [row["url"] for row in state.conn.execute("SELECT url FROM parse_results")]

    assert remaining == ["https://a.test/local.pdf"]
