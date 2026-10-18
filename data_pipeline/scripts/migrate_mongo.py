"""One-off: fill the slide's Mongo collections (html, other, graph) from the batch crawl and corpus.

Reads ``<crawl>/state/index.sqlite3`` and ``<corpus>/documents.jsonl``, ``links.jsonl`` read-only.
Old collections (pages, documents, images, links, datasets) are left alone. Delete after use.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from data_pipeline.domain.parser.records import (
    content_id_for,
    document_format,
    other_record,
    record_from_article,
)
from data_pipeline.domain.storage.documents import to_write
from data_pipeline.infrastructure.corpus.mongo import MongoCorpusDatabase

_BATCH = 10_000


def _articles(crawl: Path) -> Iterator[dict[str, Any]]:
    db = sqlite3.connect(f"file:{crawl / 'state' / 'index.sqlite3'}?mode=ro", uri=True)
    try:
        for (payload,) in db.execute("SELECT payload FROM articles ORDER BY url"):
            yield json.loads(payload)
    finally:
        db.close()


def final_urls(crawl: Path) -> dict[str, str]:
    """Request URL → final URL of every stored page (the fetcher keys records by the final URL)."""
    return {a["url"]: a.get("final_url") or a["url"] for a in _articles(crawl)}


def html_documents(
    crawl: Path, finals: Mapping[str, str], updated_at: str
) -> Iterator[dict[str, Any]]:
    db = sqlite3.connect(f"file:{crawl / 'state' / 'index.sqlite3'}?mode=ro", uri=True)
    owners = dict(db.execute("SELECT content_id, url FROM content_owner"))
    db.close()
    for article in _articles(crawl):
        record = record_from_article(finals[article["url"]], article)
        owner = owners.get(content_id_for(record["content"]))
        if record["content"].strip() and owner is not None and owner != article["url"]:
            record["content"] = ""
            record["duplicate_of"] = finals.get(owner, owner)
        record["out-links"] = []
        yield to_write(record, updated_at).document


def other_documents(corpus: Path, updated_at: str) -> Iterator[dict[str, Any]]:
    with (corpus / "documents.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            doc = json.loads(line)
            url = doc.get("final_url") or doc["url"]
            text = "\n\n".join(p["text"] for p in doc.get("parts", []) if p.get("text", "").strip())
            record = other_record(url, document_format(url, doc.get("mime_type", "")), text)
            yield to_write(record, updated_at).document


def graph_edges(corpus: Path, finals: Mapping[str, str]) -> Iterator[dict[str, str]]:
    with (corpus / "links.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            link = json.loads(line)
            if link.get("relation") == "image":
                continue
            src = link["source_url"]
            yield {
                "src": finals.get(src, src),
                "dest": link["target_url"],
                "text": link.get("anchor_text") or "",
            }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawl", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--mongodb-uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--database", default="information_retrieval")
    args = parser.parse_args(argv)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    client: MongoClient[dict[str, Any]] = MongoClient(
        args.mongodb_uri, serverSelectionTimeoutMS=5000
    )
    try:
        db = client[args.database]
        store = MongoCorpusDatabase(db)
        store.ensure_indexes()
        finals = final_urls(args.crawl)
        html = store.load("html", html_documents(args.crawl, finals, now))
        other = store.load("other", other_documents(args.corpus, now))
        db.graph.delete_many({})
        edges = graph_edges(args.corpus, finals)
        graph = 0
        while batch := list(islice(edges, _BATCH)):
            db.graph.insert_many(batch, ordered=False)
            graph += len(batch)
    finally:
        client.close()
    print(f"html={html}")
    print(f"other={other}")
    print(f"graph={graph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
