from __future__ import annotations

import time
from typing import override

import pytest

from data_pipeline import bootstrap
from data_pipeline.cli.main import main
from data_pipeline.domain.aggregator.seeds import Seeds
from data_pipeline.domain.controller.models import CrawlConfig
from data_pipeline.domain.shared.config import ProjectPaths
from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState
from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue
from data_pipeline.infrastructure.shared.lease import SqliteLease
from data_pipeline.runner import crawl as runner
from tests.fakes.controller import FakeClock
from tests.fakes.storage import FakeCorpusStore


def _project(project: ProjectPaths) -> ProjectPaths:
    config = project.root / "config"
    config.mkdir()
    (config / "controller.yaml").write_text(
        "downloaders: [http://n:8800]\ndatabase: data/state/controller.sqlite3\n", encoding="utf-8"
    )
    (config / "crawl.yaml").write_text("workers: 2\n", encoding="utf-8")
    (config / "aggregator.yaml").write_text(
        "seeds_file: config/seeds.txt\ninitial_scope_file: config/seeds.txt\n"
        "database: data/state/aggregator.sqlite3\noutbox: data/state/outbox.jsonl\n",
        encoding="utf-8",
    )
    (config / "fetcher.yaml").write_text("port: 8800\n", encoding="utf-8")
    (config / "parser.yaml").write_text("port: 6600\n", encoding="utf-8")
    (config / "seeds.txt").write_text("ctsv.hust.edu.vn\n", encoding="utf-8")
    return project


def test_crawl_run_runs_the_blocks_with_the_seeds(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(project)
    calls: list[dict[str, object]] = []

    async def fake_crawl(
        config: CrawlConfig, parts: runner.CrawlParts, **kwargs: object
    ) -> runner.CrawlSummary:
        calls.append({"workers": config.workers, **kwargs})
        return runner.CrawlSummary(sent=3, stored=2, failed=1, queued_due=0)

    monkeypatch.setattr(runner, "crawl", fake_crawl)
    assert main(["crawl", "run"]) == 0
    (call,) = calls
    assert call["workers"] == 2
    seeds = call["seeds"]
    assert isinstance(seeds, Seeds)
    assert seeds.urls == ("https://ctsv.hust.edu.vn/",)
    assert call["initial_scope"] == frozenset({"ctsv.hust.edu.vn"})
    assert call["forever"] is False
    assert "stored 2" in capsys.readouterr().out
    assert (project.root / "data/state/controller.sqlite3").exists()


def test_crawl_run_refuses_while_services_hold_the_state(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(project)
    database = project.root / "data/state/aggregator.sqlite3"
    SqliteLease(database, "dich-vu aggregator@box:7", FakeClock(time.time())).acquire()

    async def must_not_crawl(*args: object, **kwargs: object) -> runner.CrawlSummary:
        pytest.fail("must not crawl")

    monkeypatch.setattr(runner, "crawl", must_not_crawl)
    assert main(["crawl", "run"]) == 2
    assert "dich-vu aggregator@box:7" in capsys.readouterr().err


def test_seeds_add_widens_scope_and_queues(
    project: ProjectPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(project)
    assert main(["seeds", "add", "soict.hust.edu.vn", "https://ctsv.hust.edu.vn/a"]) == 0
    state = SqliteAggregatorState(project.root / "data/state/aggregator.sqlite3")
    assert {"soict.hust.edu.vn", "ctsv.hust.edu.vn"} <= state.scope_hosts()
    queue = SqliteUrlQueue(project.root / "data/state/controller.sqlite3")
    assert queue.counts(time.time()).total == 2
    assert "2 new" in capsys.readouterr().out
    assert main(["seeds", "add", "soict.hust.edu.vn"]) == 0
    assert "0 new" in capsys.readouterr().out


def test_seeds_add_reports_bad_entries(
    project: ProjectPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(project)
    assert main(["seeds", "add", "ftp://x.hust.edu.vn/"]) == 2
    assert "ftp://x.hust.edu.vn/" in capsys.readouterr().err


def test_crawl_run_closes_the_store(project: ProjectPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(project)
    closed: list[bool] = []

    class _Store(FakeCorpusStore):
        @override
        def close(self) -> None:
            closed.append(True)

    def store(uri: str, database: str) -> _Store:
        return _Store()

    async def fake_crawl(
        config: CrawlConfig, parts: runner.CrawlParts, **kwargs: object
    ) -> runner.CrawlSummary:
        return runner.CrawlSummary(sent=0, stored=0, failed=0, queued_due=0)

    monkeypatch.setattr(bootstrap, "corpus_store", store)
    monkeypatch.setattr(runner, "crawl", fake_crawl)
    assert main(["crawl", "run"]) == 0
    assert closed == [True]
