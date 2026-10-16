from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urldefrag

# The collections a snapshot is imported into, after the datasets collection.
SNAPSHOT_COLLECTIONS = ("pages", "documents", "images", "links")
# Package file -> collection.
COLLECTION_FILES = (
    ("articles.jsonl", "pages"),
    ("documents.jsonl", "documents"),
    ("images.jsonl", "images"),
    ("links.jsonl", "links"),
)
PAGE_FIELDS = (
    "url",
    "final_url",
    "title",
    "author",
    "published_at",
    "modified_at",
    "summary",
    "text",
    "language",
    "canonical_url",
    "page_role",
    "status",
    "content_type",
    "content_id",
    "duplicate_of",
    "warnings",
)


def record_id(value: dict[str, Any], kind: str) -> str:
    identity = [
        kind,
        value.get("url") or value.get("source_url"),
        value.get("target_url"),
    ]
    if kind == "links":
        identity.append(value.get("relation"))
    identity.extend(
        [
            value.get("anchor_text"),
            value.get("alt"),
            value.get("caption"),
        ]
    )
    raw = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def page_value(value: dict[str, Any]) -> dict[str, Any]:
    """Keep Mongo page records focused on searchable metadata and plain text."""
    return {key: value[key] for key in PAGE_FIELDS if key in value}


def snapshot_record(value: dict[str, Any], collection: str, snapshot_id: str) -> dict[str, Any]:
    """One package row as stored in ``collection`` for ``snapshot_id``."""
    if collection == "pages":
        value = page_value(value)
    value["snapshot_id"] = snapshot_id
    value["_id"] = record_id(value, collection) + ":" + snapshot_id
    return value


def normalize_query_url(url: str) -> str:
    """The graph stores URLs without fragments; normalise a query the same way."""
    return urldefrag(url.strip())[0]
