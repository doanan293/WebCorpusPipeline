"""A small batch-crawl output with the real schema, for the one-off migration scripts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

FINISHED_AT = 1_792_238_868.0


def _record(
    url: str, status: str, purpose: str = "page", **extra: object
) -> tuple[str, str, str, int]:
    payload = {"url": url, "status": status, "response_purpose": purpose, **extra}
    return (url, json.dumps(payload), status, 1)


def _article(url: str, text: str, final_url: str | None = None) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "url": url,
        "final_url": final_url or url,
        "title": f"T {url}",
        "text": text,
        "summary": "gioi thieu",
        "author": None,
        "published_at": "2026-01-01",
        "modified_at": None,
        "language": "vi",
        "headings": [],
        "content_blocks": [],
        "assets": [{"url": "https://h.hust.edu.vn/anh.jpg", "role": "inline_image"}],
        "observed_refs": [
            {"url": "https://h.hust.edu.vn/b", "text": "B", "kind": "page", "rel": []}
        ],
        "content_id": "ignored",
    }
    return (url, json.dumps(payload))


def content_id_for(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@pytest.fixture
def crawl(tmp_path: Path) -> Path:
    root = tmp_path / "crawl"
    (root / "state").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"finished_at": FINISHED_AT}))
    db = sqlite3.connect(root / "state" / "index.sqlite3")
    db.executescript(
        """
        CREATE TABLE url_records (url TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                                  completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE raw_pages (url TEXT PRIMARY KEY, final_url TEXT NOT NULL, status INTEGER NOT NULL,
                                content_type TEXT NOT NULL, fetched_at TEXT NOT NULL, body_sha256 TEXT NOT NULL);
        CREATE TABLE articles (url TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE content_owner (content_id TEXT PRIMARY KEY, url TEXT NOT NULL);
        """
    )
    records = [
        _record("https://h.hust.edu.vn/", "extracted"),
        _record("https://h.hust.edu.vn/a", "extracted", final_url="https://h.hust.edu.vn/a2"),
        _record("https://h.hust.edu.vn/dup", "extracted"),
        _record("https://h.hust.edu.vn/next", "scheduled"),
        _record("https://h.hust.edu.vn/robots.txt", "scheduled", "robots"),
        _record("https://h.hust.edu.vn/broken", "failed"),
        _record("https://h.hust.edu.vn/sitemap.xml", "failed", "sitemap"),
        _record("https://h.hust.edu.vn/qd.pdf", "discovered_not_downloaded"),
        _record("https://h.hust.edu.vn/anh.jpg", "discovered_not_downloaded"),
        _record("https://google.com/", "skipped"),
        _record("https://h.hust.edu.vn/gone", "absent"),
    ]
    db.executemany("INSERT INTO url_records VALUES (?, ?, ?, ?)", records)
    db.execute(
        "INSERT INTO raw_pages VALUES ('https://h.hust.edu.vn/', 'https://h.hust.edu.vn/', 200, "
        "'text/html', '2026-10-16T03:14:31+00:00', 'x')"
    )
    db.executemany(
        "INSERT INTO articles VALUES (?, ?)",
        [
            _article("https://h.hust.edu.vn/", "home"),
            _article("https://h.hust.edu.vn/a", "same", "https://h.hust.edu.vn/a2"),
            _article("https://h.hust.edu.vn/dup", "same"),
        ],
    )
    db.executemany(
        "INSERT INTO content_owner VALUES (?, ?)",
        [
            (content_id_for("home"), "https://h.hust.edu.vn/"),
            (content_id_for("same"), "https://h.hust.edu.vn/a"),
        ],
    )
    db.commit()
    db.close()
    return root
