from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urldefrag


def query_links(
    db: Any,
    snapshot_id: str,
    url: str,
    *,
    direction: Literal["incoming", "outgoing"],
    limit: int,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` links into or out of ``url`` in a ready snapshot."""
    dataset = db.datasets.find_one({"_id": snapshot_id})
    if not dataset or dataset.get("status") != "ready":
        msg = f"snapshot {snapshot_id} is not ready in MongoDB"
        raise ValueError(msg)
    # The graph stores URLs without fragments; normalise the query the same way.
    target = urldefrag(url.strip())[0]
    field = "target_url" if direction == "incoming" else "source_url"
    return list(db.links.find({"snapshot_id": snapshot_id, field: target}, {"_id": 0}, limit=limit))
