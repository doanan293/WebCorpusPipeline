from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from data_pipeline.corpus.application.ports import EdgeIndexFactory
from data_pipeline.corpus.domain.graph import page_edges


def build_graph(
    articles: Iterable[dict[str, Any]], output: Path, open_index: EdgeIndexFactory
) -> dict[str, int]:
    """Write ``links.jsonl`` and ``images.jsonl`` for the pages in ``articles``; return counts."""
    with open_index(output) as index:
        for row in articles:
            edges, images = page_edges(row)
            index.add(edges, images)
        return index.write(output)
