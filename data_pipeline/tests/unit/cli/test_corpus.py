from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from data_pipeline import bootstrap
from data_pipeline.application.corpus import graph, kaggle, mongo, package, query
from data_pipeline.application.enrich import session as enrich_session
from data_pipeline.cli.main import main
from data_pipeline.domain.shared.config import ProjectPaths, Settings
from data_pipeline.infrastructure.corpus import mongo as mongo_adapter
from data_pipeline.infrastructure.shared.settings import project_paths


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    @contextmanager
    def open_work_state(settings: Settings, *, rebind: bool, **_: object) -> Iterator[object]:
        seen["settings"] = settings
        seen["rebind"] = rebind
        seen["state"] = state = object()
        yield state

    monkeypatch.setattr(enrich_session, "open_work_state", open_work_state)
    return seen


def test_package_uses_the_default_source_and_work_directories(
    recorded: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, object, Path]] = []

    def package_corpus(source: Path, state: object, output: Path, **_: object) -> None:
        calls.append((source, state, output))

    monkeypatch.setattr(package, "package_corpus", package_corpus)

    assert main(["corpus", "package", "--output", str(tmp_path / "out")]) == 0

    settings = recorded["settings"]
    assert isinstance(settings, Settings)
    assert settings.work_path == project_paths().enrichment
    assert calls == [(settings.source_path, recorded["state"], tmp_path / "out")]


def test_build_graph_passes_the_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def build_graph(articles: object, output: Path, open_index: object) -> None:
        calls.append(output)

    monkeypatch.setattr(graph, "build_graph", build_graph)

    assert main(["corpus", "build-graph", "--output", str(tmp_path / "g")]) == 0
    assert calls == [tmp_path / "g"]


def test_verify_prints_the_report_and_succeeds_for_a_valid_corpus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def valid_corpus(corpus: Path, files: object) -> dict[str, Any]:
        return {"valid": True, "errors": []}

    monkeypatch.setattr(package, "verify_corpus", valid_corpus)

    assert main(["corpus", "verify", "--corpus", "c"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_verify_exits_3_for_an_invalid_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_corpus(corpus: Path, files: object) -> dict[str, Any]:
        return {"valid": False, "errors": ["missing manifest"]}

    monkeypatch.setattr(package, "verify_corpus", invalid_corpus)

    assert main(["corpus", "verify", "--corpus", "c"]) == 3


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.database = object()

    def __getitem__(self, name: str) -> object:
        return self.database

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()

    def connect(settings: Settings) -> FakeClient:
        return fake

    monkeypatch.setattr(bootstrap, "connect", connect)
    return fake


def test_import_mongo_passes_force(
    client: FakeClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, object, bool]] = []

    def import_corpus(corpus: Path, db: object, files: object, *, force: bool) -> dict[str, str]:
        assert isinstance(db, mongo_adapter.MongoCorpusDatabase)
        calls.append((corpus, db.db, force))
        return {"status": "ready"}

    monkeypatch.setattr(mongo, "import_corpus", import_corpus)

    assert main(["corpus", "import-mongo", "--corpus", str(tmp_path / "c"), "--force"]) == 0
    assert calls == [(tmp_path / "c", client.database, True)]
    assert client.closed


@pytest.mark.parametrize(
    ("flags", "direction", "limit"),
    [([], "incoming", 100), (["--direction", "outgoing", "--limit", "5"], "outgoing", 5)],
)
def test_query_direction_and_limit(
    client: FakeClient,
    flags: list[str],
    direction: str,
    limit: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, int]] = []

    def query_links(
        db: object, snapshot: str, url: str, *, direction: str, limit: int
    ) -> list[object]:
        calls.append((snapshot, url, direction, limit))
        return []

    monkeypatch.setattr(query, "query_links", query_links)

    assert main(["corpus", "query", "--snapshot", "s", "--url", "https://a.test/", *flags]) == 0
    assert calls == [("s", "https://a.test/", direction, limit)]


def test_prune_mongo_keeps_the_given_snapshot(
    client: FakeClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def prune_snapshots(db: object, keep: str) -> dict[str, int]:
        return {"links": 2, "kept": int(keep == "s1")}

    monkeypatch.setattr(mongo, "prune_snapshots", prune_snapshots)

    assert main(["corpus", "prune-mongo", "--keep", "s1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"links": 2, "kept": 1}


def test_publish_defaults_to_the_project_kaggle_config(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path, bool, str | None]] = []

    def publish_corpus(
        corpus: Path,
        config: Path,
        publisher: object,
        files: object,
        *,
        update: bool,
        message: str | None,
    ) -> dict[str, str]:
        calls.append((corpus, config, update, message))
        return {"status": "ok"}

    monkeypatch.setattr(kaggle, "publish_corpus", publish_corpus)

    code = main(
        ["corpus", "publish", "--corpus", str(tmp_path / "c"), "--update", "--message", "m"]
    )

    assert code == 0
    assert calls == [(tmp_path / "c", project.kaggle_config, True, "m")]


def test_pull_defaults_to_the_project_lock_and_destination(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []

    def pull_corpus(lock: Path, destination: Path, publisher: object, files: object) -> str:
        calls.append((lock, destination))
        return "ok"

    monkeypatch.setattr(kaggle, "pull_corpus", pull_corpus)

    assert main(["corpus", "pull"]) == 0
    assert calls == [(project.corpus_lock, project.corpus_pulled)]
