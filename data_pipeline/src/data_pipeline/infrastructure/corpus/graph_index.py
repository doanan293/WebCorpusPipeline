from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from data_pipeline.domain.corpus.graph import Edge

EDGE_BATCH_SIZE = 5000


class SqliteEdgeIndex:
    """Edge deduplication on disk: a crawl can hold millions of references."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.unlink(missing_ok=True)  # left behind by a killed run
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(
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
        self._edges: list[Edge] = []
        self._images: list[tuple[str]] = []

    def add(self, edges: list[Edge], images: list[str]) -> None:
        self._edges.extend(edges)
        self._images.extend((url,) for url in images)
        if len(self._edges) >= EDGE_BATCH_SIZE:
            self._flush()

    def _flush(self) -> None:
        if self._edges:
            self.conn.executemany("INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?)", self._edges)
            self._edges.clear()
        if self._images:
            self.conn.executemany("INSERT OR IGNORE INTO images VALUES (?)", self._images)
            self._images.clear()
        self.conn.commit()

    def write(self, output: Path) -> dict[str, int]:
        """Write ``links.jsonl`` and ``images.jsonl`` sorted; return their counts."""
        self._flush()
        with (output / "links.jsonl").open("w", encoding="utf-8") as out:
            for row in self.conn.execute(
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
            for (url,) in self.conn.execute("SELECT url FROM images ORDER BY url"):
                out.write(json.dumps({"url": url}, ensure_ascii=False) + "\n")
        return {
            "links": self.conn.execute("SELECT count(*) FROM edges").fetchone()[0],
            "images": self.conn.execute("SELECT count(*) FROM images").fetchone()[0],
        }

    def close(self) -> None:
        self.conn.close()
        self.db_path.unlink(missing_ok=True)


@contextmanager
def open_edge_index(output: Path) -> Iterator[SqliteEdgeIndex]:
    """An edge index in ``output/.graph.sqlite3``, deleted when done."""
    output.mkdir(parents=True, exist_ok=True)
    index = SqliteEdgeIndex(output / ".graph.sqlite3")
    try:
        yield index
    finally:
        index.close()
