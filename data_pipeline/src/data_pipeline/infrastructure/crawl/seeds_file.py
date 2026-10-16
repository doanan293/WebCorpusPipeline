from __future__ import annotations

from pathlib import Path

from data_pipeline.domain.crawl.seeds import SeedSet, parse_seed_lines


def parse_seed_file(path: Path) -> SeedSet:
    return parse_seed_lines(path.read_text(encoding="utf-8").splitlines())
