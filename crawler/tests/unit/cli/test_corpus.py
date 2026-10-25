from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from crawler import bootstrap
from crawler.application.corpus import kaggle, load, package
from crawler.cli.main import main
from crawler.domain.shared.config import ProjectPaths, Settings
from tests.fakes.corpus import FakeCorpusDatabase


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> FakeCorpusDatabase:
    db = FakeCorpusDatabase()

    @contextmanager
    def mongo_database(_: Settings) -> Iterator[FakeCorpusDatabase]:
        yield db

    monkeypatch.setattr(bootstrap, "mongo_database", mongo_database)
    return db


def test_package_exports_mongo_to_the_output(
    project: ProjectPaths, tmp_path: Path, database: FakeCorpusDatabase
) -> None:
    database.load(
        "html",
        [{"url": "https://h/a", "format": "html", "title": "A", "content": "a", "intro": ""}],
    )
    assert main(["corpus", "package", "--output", str(tmp_path / "corpus")]) == 0
    manifest = json.loads((tmp_path / "corpus" / "manifest.json").read_text())
    assert manifest["counts"]["html.jsonl"] == 1
    assert manifest["archives"] == {}  # the project has no data/ folders yet


def test_package_packs_data_folders_unless_told_not_to(
    project: ProjectPaths, tmp_path: Path, database: FakeCorpusDatabase
) -> None:
    (project.data_root / "state").mkdir(parents=True)
    (project.data_root / "state" / "mediator.sqlite3").write_bytes(b"q")
    assert main(["corpus", "package", "--output", str(tmp_path / "a")]) == 0
    assert (
        "data-state.tar.gz.bin"
        in json.loads((tmp_path / "a" / "manifest.json").read_text())["archives"]
    )
    assert main(["corpus", "package", "--output", str(tmp_path / "b"), "--no-data"]) == 0
    assert json.loads((tmp_path / "b" / "manifest.json").read_text())["archives"] == {}


def _report(valid: bool) -> Any:
    def verify_corpus(path: Path, files: object) -> dict[str, Any]:
        return {"valid": valid, "errors": [] if valid else ["x"]}

    return verify_corpus


def test_verify_prints_the_report_and_succeeds_for_a_valid_corpus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(package, "verify_corpus", _report(valid=True))
    assert main(["corpus", "verify", "--corpus", "c"]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "errors": []}


def test_verify_exits_3_for_an_invalid_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package, "verify_corpus", _report(valid=False))
    assert main(["corpus", "verify", "--corpus", "c"]) == 3


def test_query_direction_and_limit(
    project: ProjectPaths, database: FakeCorpusDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    database.load(
        "graph",
        [
            {"src": "https://h/a", "dest": "https://h/b", "intro": "B"},
            {"src": "https://h/a", "dest": "https://h/c", "intro": "C"},
        ],
    )
    code = main(
        ["corpus", "query", "--url", "https://h/a", "--direction", "outgoing", "--limit", "1"]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == [
        {"src": "https://h/a", "dest": "https://h/b", "intro": "B"}
    ]


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


def test_pull_downloads_then_loads_into_mongo(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch, database: FakeCorpusDatabase
) -> None:
    calls: list[Any] = []

    def pull_corpus(lock: Path, destination: Path, publisher: object, files: object) -> Path:
        calls.append(("pull", lock, destination))
        return destination

    def load_corpus(path: Path, db: object, files: object) -> dict[str, int]:
        calls.append(("load", path, db))
        return {"html": 1, "other": 0, "graph": 0}

    def restore_data(path: Path, data_root: Path, files: object) -> dict[str, str]:
        calls.append(("restore", path, data_root))
        return {"state": "restored"}

    monkeypatch.setattr(kaggle, "pull_corpus", pull_corpus)
    monkeypatch.setattr(load, "load_corpus", load_corpus)
    monkeypatch.setattr(package, "restore_data", restore_data)
    assert main(["corpus", "pull"]) == 0
    assert calls == [
        ("pull", project.corpus_lock, project.corpus_pulled),
        ("load", project.corpus_pulled, database),
        ("restore", project.corpus_pulled, project.data_root),
    ]
    calls.clear()
    assert main(["corpus", "pull", "--no-data"]) == 0
    assert [call[0] for call in calls] == ["pull", "load"]


def test_pull_no_load_skips_mongo(project: ProjectPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    def pull_corpus(lock: Path, destination: Path, publisher: object, files: object) -> Path:
        return destination

    monkeypatch.setattr(kaggle, "pull_corpus", pull_corpus)

    def load_corpus(*_: object) -> dict[str, int]:
        pytest.fail("must not load")

    monkeypatch.setattr(load, "load_corpus", load_corpus)
    assert main(["corpus", "pull", "--no-load"]) == 0
