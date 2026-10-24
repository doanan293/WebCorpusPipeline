"""How one ``/add`` record lands in Mongo: ``html`` or ``other``, plus its ``graph`` edges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

HTML_FIELDS = (
    "url", "format", "title", "content", "intro", "published_at", "modified_at", "author",
    "language", "headings", "content_blocks", "images", "content_id", "duplicate_of",
)  # fmt: skip
OTHER_FIELDS = ("url", "format", "content", "title", "content_id")


@dataclass(frozen=True, slots=True)
class StorageWrite:
    collection: Literal["html", "other"]
    document: dict[str, Any]
    edges: list[dict[str, Any]]


def to_write(record: Mapping[str, Any], updated_at: str) -> StorageWrite:
    """The teacher's schema: known fields only, ``intro`` present on ``html`` (storage fills it)."""
    is_html = record["format"] == "html"
    fields = HTML_FIELDS if is_html else OTHER_FIELDS
    document = {name: record[name] for name in fields if name in record}
    if is_html:
        document.setdefault("intro", "")
    # A record reaches this function only when it is written: new, or changed since last time.
    document["updated_at"] = document["checked_at"] = document["changed_at"] = updated_at
    edges = [
        {"src": record["url"], "dest": dest, "intro": text} for dest, text in record["out-links"]
    ]
    return StorageWrite("html" if is_html else "other", document, edges)
