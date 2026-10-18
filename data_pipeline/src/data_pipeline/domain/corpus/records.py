from __future__ import annotations

from urllib.parse import urldefrag

# The slide's Mongo collections, which are also the files of a corpus package.
CORPUS_COLLECTIONS = ("html", "other", "graph")


def collection_file(name: str) -> str:
    return f"{name}.jsonl"


def normalize_query_url(url: str) -> str:
    """The graph stores URLs without fragments; normalise a query the same way."""
    return urldefrag(url.strip())[0]
