from __future__ import annotations

import asyncio
import gzip
import hashlib
import sqlite3
from pathlib import Path

from data_pipeline.application.parser.reextract import reextract
from data_pipeline.infrastructure.parser.raw_pages import SqliteRawPages
from tests.fakes.parser import FakeAggregatorClient

PAGE = "<html><head><title>{t}</title></head><body><article><p>{t} noi dung</p></article></body></html>"


def _crawl(tmp_path: Path) -> Path:
    crawl = tmp_path / "crawl"
    (crawl / "state").mkdir(parents=True)
    db = sqlite3.connect(crawl / "state" / "index.sqlite3")
    db.execute(
        "CREATE TABLE raw_pages (url TEXT PRIMARY KEY, final_url TEXT NOT NULL, status INTEGER NOT NULL,"
        " content_type TEXT NOT NULL, fetched_at TEXT NOT NULL, body_sha256 TEXT NOT NULL)"
    )
    for title in ("A", "B"):
        body = PAGE.format(t=title).encode()
        sha = hashlib.sha256(body).hexdigest()
        path = crawl / "html" / sha[:2] / f"{sha}.html.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))
        db.execute(
            "INSERT INTO raw_pages VALUES (?, ?, 200, 'text/html; charset=utf-8', '2026-10-16T00:00:00+00:00', ?)",
            (f"https://h/{title}", f"https://h/{title}", sha),
        )
    db.commit()
    db.close()
    return crawl


def test_reextract_sends_records(tmp_path: Path) -> None:
    aggregator = FakeAggregatorClient()
    sent = asyncio.run(reextract(SqliteRawPages(_crawl(tmp_path)), aggregator))
    assert sent == 2
    assert sorted(record["title"] for record in aggregator.records) == ["A", "B"]
    assert {record["url"] for record in aggregator.records} == {"https://h/A", "https://h/B"}
