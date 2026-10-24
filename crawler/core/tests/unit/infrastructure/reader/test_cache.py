from __future__ import annotations

from pathlib import Path

from data_pipeline.domain.reader.models import ExtractedPart, ParseResult
from data_pipeline.infrastructure.reader.cache import read_cached, write_cached


def test_cache_round_trips_a_result(tmp_path: Path) -> None:
    result = ParseResult(
        "abc",
        [ExtractedPart(1, "page", "chu", reader="llamaparse-fast")],
        "extracted",
        [],
        {"llamaparse": "fast/latest"},
    )
    write_cached(tmp_path, "k1", result)
    assert read_cached(tmp_path, "k1") == result
