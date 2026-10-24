from __future__ import annotations

from typing import Any, Literal

from crawler.application.corpus.ports import CorpusDatabase
from crawler.domain.corpus.records import normalize_query_url


def query_links(
    db: CorpusDatabase,
    url: str,
    *,
    direction: Literal["incoming", "outgoing"],
    limit: int,
) -> list[dict[str, Any]]:
    """Up to ``limit`` graph edges into or out of ``url``."""
    target = normalize_query_url(url)
    if direction == "incoming":
        return db.find_edges(src=None, dest=target, limit=limit)
    return db.find_edges(src=target, dest=None, limit=limit)
