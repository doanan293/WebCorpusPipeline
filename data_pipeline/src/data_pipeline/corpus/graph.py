from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from data_pipeline.core.refs import classify_ref

EDGE_BATCH_SIZE = 5000


def _url(value: object) -> str:
    return urldefrag(str(value or "").strip())[0]


def build_graph(source: Path, output: Path) -> dict[str, Any]:
    articles = source / "articles.jsonl" if source.is_dir() else source
    output.mkdir(parents=True, exist_ok=True)
    # A crawl can contain millions of references.  Keep the deduplication index
    # on disk instead of retaining the complete edge set in Python memory.
    db_path = output / ".graph.sqlite3"
    db_path.unlink(missing_ok=True)  # left behind by a killed run
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE edges (
          source_url TEXT NOT NULL, target_url TEXT NOT NULL, relation TEXT NOT NULL,
          anchor_text TEXT NOT NULL, alt TEXT NOT NULL, caption TEXT NOT NULL,
          PRIMARY KEY(source_url,target_url,relation,anchor_text,alt,caption)
        );
        CREATE TABLE images (url TEXT PRIMARY KEY);
        """
    )
    edge_batch: list[tuple[str, str, str, str, str, str]] = []
    image_batch: list[tuple[str]] = []

    def add(page: str, item: dict[str, Any], *, role: str | None, kind: str | None) -> None:
        target = _url(item.get("url"))
        relation = classify_ref(target, role=role, kind=kind)
        edge_batch.append(
            (
                page,
                target,
                relation,
                str(item.get("text") or "") if kind is not None else "",
                str(item.get("alt") or ""),
                str(item.get("caption") or "") if role is not None else "",
            )
        )
        if relation == "image":
            image_batch.append((target,))

    def flush() -> None:
        if edge_batch:
            conn.executemany("INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?)", edge_batch)
            edge_batch.clear()
        if image_batch:
            conn.executemany("INSERT OR IGNORE INTO images VALUES (?)", image_batch)
            image_batch.clear()
        conn.commit()

    try:
        with articles.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                page = _url(row.get("url") or row.get("final_url"))
                if not page:
                    continue
                for item in row.get("links") or []:
                    target = _url(item.get("url")) if isinstance(item, dict) else ""
                    if target:
                        edge_batch.append(
                            (page, target, "hyperlink", str(item.get("text") or ""), "", "")
                        )
                for item in row.get("assets") or []:
                    if isinstance(item, dict) and _url(item.get("url")):
                        add(page, item, role=str(item.get("role") or "asset"), kind=None)
                for item in row.get("observed_refs") or []:
                    if isinstance(item, dict) and _url(item.get("url")):
                        add(page, item, role=None, kind=str(item.get("kind") or ""))
                if len(edge_batch) >= EDGE_BATCH_SIZE:
                    flush()
        flush()
        with (output / "links.jsonl").open("w", encoding="utf-8") as out:
            for row in conn.execute(
                "SELECT source_url,target_url,relation,anchor_text,alt,caption FROM edges "
                "ORDER BY source_url,target_url,relation,anchor_text,alt,caption"
            ):
                out.write(
                    json.dumps(
                        {
                            "source_url": row[0],
                            "target_url": row[1],
                            "relation": row[2],
                            "anchor_text": row[3],
                            "alt": row[4],
                            "caption": row[5],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        with (output / "images.jsonl").open("w", encoding="utf-8") as out:
            for (url,) in conn.execute("SELECT url FROM images ORDER BY url"):
                out.write(json.dumps({"url": url}, ensure_ascii=False) + "\n")
        return {
            "links": conn.execute("SELECT count(*) FROM edges").fetchone()[0],
            "images": conn.execute("SELECT count(*) FROM images").fetchone()[0],
        }
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)
