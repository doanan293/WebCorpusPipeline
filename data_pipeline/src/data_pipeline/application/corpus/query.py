from __future__ import annotations

from typing import Any, Literal

from data_pipeline.application.corpus.ports import CorpusDatabase
from data_pipeline.domain.corpus.records import normalize_query_url


def query_links(
    db: CorpusDatabase,
    snapshot_id: str,
    url: str,
    *,
    direction: Literal["incoming", "outgoing"],
    limit: int,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` links into or out of ``url`` in a ready snapshot."""
    dataset = db.dataset(snapshot_id)
    if not dataset or dataset.get("status") != "ready":
        msg = f"snapshot {snapshot_id} is not ready in MongoDB"
        raise ValueError(msg)
    field = "target_url" if direction == "incoming" else "source_url"
    return db.find_links(snapshot_id, field, normalize_query_url(url), limit)
