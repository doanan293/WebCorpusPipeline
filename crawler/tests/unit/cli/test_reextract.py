from __future__ import annotations

import gzip
import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from crawler import bootstrap
from crawler.application.reader import reextract as reextract_module
from crawler.cli.main import main
from crawler.domain.shared.config import ProjectPaths
from crawler.infra.mediator.sqlite_queue import SqliteUrlQueue
from tests.fakes.storage import FakeCorpusStore
from tests.unit.cli.test_crawl import _project

PAGE = (
    "<html><head><meta charset='utf-8'><title>{t}</title></head><body><article><h1>{t}</h1>"
    "<p>Bai {t} noi dung rieng. Xem <a href='/C'>trang C</a> ngay hôm nay.</p>"
    "</article></body></html>"
)


def _crawl(tmp_path: Path) -> Path:
    crawl = tmp_path / "crawl"
    (crawl / "state").mkdir(parents=True)
    db = sqlite3.connect(crawl / "state" / "index.sqlite3")
    db.execute(
        "CREATE TABLE raw_pages (url TEXT PRIMARY KEY, final_url TEXT NOT NULL, status INTEGER,"
        " content_type TEXT NOT NULL, fetched_at TEXT NOT NULL, body_sha256 TEXT NOT NULL)"
    )
    for title in ("A", "B"):
        body = PAGE.format(t=title).encode()
        sha = hashlib.sha256(body).hexdigest()
        path = crawl / "html" / sha[:2] / f"{sha}.html.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))
        url = f"https://ctsv.hust.edu.vn/{title}"
        db.execute(
            "INSERT INTO raw_pages VALUES (?, ?, 200, 'text/html; charset=utf-8', 'x', ?)",
            (url, url, sha),
        )
    db.commit()
    db.close()
    return crawl


def test_reextract_reads_the_crawl_and_posts_to_the_merger(
    project: ProjectPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[tuple[object, object]] = []

    async def fake(pages: object, merger: object) -> int:
        seen.append((getattr(pages, "crawl", None), getattr(merger, "url", None)))
        return 7

    monkeypatch.setattr(reextract_module, "reextract", fake)
    code = main(["reextract", "--crawl", str(tmp_path / "crawl"), "--merger", "http://agg:3300"])
    assert code == 0
    assert seen == [(tmp_path / "crawl", "http://agg:3300/add")]
    assert "7" in capsys.readouterr().out


def test_reextract_without_merger_runs_in_process(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(project)
    crawl = _crawl(tmp_path)
    store = FakeCorpusStore()

    def fake_store(uri: str, database: str) -> FakeCorpusStore:
        return store

    monkeypatch.setattr(bootstrap, "corpus_store", fake_store)
    assert main(["reextract", "--crawl", str(crawl)]) == 0
    assert set(store.collections["html"]) == {
        "https://ctsv.hust.edu.vn/A",
        "https://ctsv.hust.edu.vn/B",
    }
    assert {edge["intro"] for edge in store.graph} == {"Xem trang C ngay hôm nay."}
    queue = SqliteUrlQueue(project.root / "data/state/mediator.sqlite3")
    assert queue.counts(time.time()).total == 1  # the new link, queued as the services would
