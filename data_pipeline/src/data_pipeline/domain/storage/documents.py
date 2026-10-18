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
    """The slide's schema: known fields only, ``intro`` always present on ``html``."""
    is_html = record["format"] == "html"
    fields = HTML_FIELDS if is_html else OTHER_FIELDS
    document = {name: record[name] for name in fields if name in record}
    if is_html:
        document.setdefault("intro", "")
    document["updated_at"] = updated_at
    edges = [
        {"src": record["url"], "dest": dest, "text": text} for dest, text in record["out-links"]
    ]
    return StorageWrite("html" if is_html else "other", document, edges)
