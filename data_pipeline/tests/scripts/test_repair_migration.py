from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from data_pipeline.domain.parser.records import content_id_for
from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from scripts import repair_migration

MIGRATED = "2026-10-18T07:00:00+00:00"
SAME = "noi dung giong nhau"


def _legacy(tmp_path: Path) -> sqlite3.Connection:
    """connect: the owner is the http URL; the https one is its empty duplicate (sorts after)."""
    db = sqlite3.connect(tmp_path / "index.sqlite3")
    db.execute("CREATE TABLE articles (url TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    db.execute("CREATE TABLE content_owner (content_id TEXT PRIMARY KEY, url TEXT NOT NULL)")
    cid = content_id_for("Welcome to connect")
    articles = [
        {
            "url": "http://c/",
            "final_url": "https://c/",
            "title": "C",
            "text": "Welcome to connect",
            "summary": "gioi thieu",
            "content_id": cid,
        },
        {
            "url": "https://c/",
            "final_url": "https://c/",
            "title": "C",
            "content_id": cid,
            "duplicate_of": "http://c/",
        },
        {
            "url": "https://h/a",
            "final_url": "https://h/a",
            "title": "A",
            "text": SAME,
            "content_id": content_id_for(SAME),
        },
        {
            "url": "https://h/b",
            "final_url": "https://h/b",
            "title": "B",
            "content_id": content_id_for(SAME),
            "duplicate_of": "https://h/a",
        },
        {
            "url": "https://h/new",
            "final_url": "https://h/new",
            "title": "N",
            "text": "old text",
            "content_id": content_id_for("old text"),
        },
        {"url": "https://h/empty", "final_url": "https://h/empty", "title": "E", "text": ""},
    ]
    db.executemany(
        "INSERT INTO articles VALUES (?, ?)", [(a["url"], json.dumps(a)) for a in articles]
    )
    db.executemany(
        "INSERT INTO content_owner VALUES (?, ?)",
        [
            (cid, "http://c/"),
            (content_id_for(SAME), "https://h/a"),
            (content_id_for("old text"), "https://h/new"),
        ],
    )
    db.commit()
    return db


class _Html:
    """A stand-in for the html collection: what the migration left, keyed by url."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {
            "https://c/": {
                "url": "https://c/",
                "title": "C",
                "content": "",
                "updated_at": MIGRATED,
            },
            "https://h/a": {"url": "https://h/a", "content": SAME, "updated_at": MIGRATED},
            "https://h/b": {"url": "https://h/b", "content": "", "updated_at": MIGRATED},
            "https://h/new": {
                "url": "https://h/new",
                "content": "",
                "updated_at": "2026-10-18T09:00:00+00:00",
            },
            "https://h/empty": {"url": "https://h/empty", "content": "", "updated_at": MIGRATED},
        }

    def apply(self, url: str, update: dict[str, Any]) -> None:
        self.docs[url].update(update)


def test_empty_doc_is_filled_from_article_with_text(tmp_path: Path) -> None:
    html = _Html()
    plan = repair_migration.plan_html(_legacy(tmp_path), html.docs.values(), MIGRATED)
    for url, update in plan.updates:
        html.apply(url, update)
    assert html.docs["https://c/"]["content"] == "Welcome to connect"
    assert html.docs["https://c/"]["intro"] == "gioi thieu"
    assert "duplicate_of" not in html.docs["https://c/"]
    assert html.docs["https://c/"]["updated_at"] == MIGRATED


def test_duplicate_gets_duplicate_of_owner_final_url(tmp_path: Path) -> None:
    html = _Html()
    plan = repair_migration.plan_html(_legacy(tmp_path), html.docs.values(), MIGRATED)
    for url, update in plan.updates:
        html.apply(url, update)
    assert html.docs["https://h/b"]["duplicate_of"] == "https://h/a"
    assert html.docs["https://h/b"]["content"] == ""
    assert html.docs["https://h/empty"] == {
        "url": "https://h/empty",
        "content": "",
        "updated_at": MIGRATED,
    }


def test_newer_doc_is_left_alone(tmp_path: Path) -> None:
    html = _Html()
    plan = repair_migration.plan_html(_legacy(tmp_path), html.docs.values(), MIGRATED)
    assert "https://h/new" not in {url for url, _ in plan.updates}
    assert plan.report.skipped_newer == 1


def test_rerun_changes_nothing(tmp_path: Path) -> None:
    html = _Html()
    legacy = _legacy(tmp_path)
    first = repair_migration.plan_html(legacy, html.docs.values(), MIGRATED)
    for url, update in first.updates:
        html.apply(url, update)
    assert (first.report.filled, first.report.duplicate_of_set) == (1, 1)
    second = repair_migration.plan_html(legacy, html.docs.values(), MIGRATED)
    assert second.updates == []


def test_owner_moves_to_final_url(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    state = SqliteAggregatorState(tmp_path / "aggregator.sqlite3")
    cid = content_id_for("Welcome to connect")
    state.claim(cid, "http://c/")
    state.claim(content_id_for(SAME), "https://h/a")
    state.close()
    db = sqlite3.connect(tmp_path / "aggregator.sqlite3")
    assert repair_migration.repair_owners(db, repair_migration.final_urls(legacy)) == 1
    assert repair_migration.repair_owners(db, repair_migration.final_urls(legacy)) == 0
    reopened = SqliteAggregatorState(tmp_path / "aggregator.sqlite3")
    assert reopened.claim(cid, "https://c/") == "https://c/"
