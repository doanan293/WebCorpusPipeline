from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.cli.main import main
from data_pipeline.core.paths import ProjectPaths
from data_pipeline.core.settings import Settings
from data_pipeline.corpus import graph, kaggle, mongo, package, query
from data_pipeline.enrich import session as enrich_session


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    @contextmanager
    def open_work_state(settings: Settings, *, rebind: bool) -> Iterator[object]:
        seen["settings"] = settings
        seen["rebind"] = rebind
        yield object()

    monkeypatch.setattr(enrich_session, "open_work_state", open_work_state)
    return seen


def test_package_uses_the_default_source_and_work_directories(
    recorded: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path, Path]] = []

    def package_corpus(source: Path, work: Path, output: Path) -> None:
        calls.append((source, work, output))

    monkeypatch.setattr(package, "package_corpus", package_corpus)

    assert main(["corpus", "package", "--output", str(tmp_path / "out")]) == 0

    settings = recorded["settings"]
    assert isinstance(settings, Settings)
    assert calls == [(settings.source_path, settings.work_path, tmp_path / "out")]


def test_build_graph_passes_the_output_directory(
    recorded: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def build_graph(source: Path, output: Path) -> None:
        calls.append(output)

    monkeypatch.setattr(graph, "build_graph", build_graph)

    assert main(["corpus", "build-graph", "--output", str(tmp_path / "g"), "--rebind-source"]) == 0
    assert calls == [tmp_path / "g"]
    assert recorded["rebind"] is True


def test_verify_prints_the_report_and_succeeds_for_a_valid_corpus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def valid_corpus(corpus: Path) -> dict[str, Any]:
        return {"valid": True, "errors": []}

    monkeypatch.setattr(package, "verify_corpus", valid_corpus)

    assert main(["corpus", "verify", "--corpus", "c"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_verify_exits_3_for_an_invalid_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_corpus(corpus: Path) -> dict[str, Any]:
        return {"valid": False, "errors": ["missing manifest"]}

    monkeypatch.setattr(package, "verify_corpus", invalid_corpus)

    assert main(["corpus", "verify", "--corpus", "c"]) == 3


def test_import_mongo_passes_project_settings(
    project: ProjectPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Path, Settings]] = []

    def import_corpus(corpus: Path, settings: Settings) -> dict[str, int]:
        calls.append((corpus, settings))
        return {"documents": 1}

    monkeypatch.setattr(mongo, "import_corpus", import_corpus)

    assert main(["corpus", "import-mongo", "--corpus", str(tmp_path / "c")]) == 0
    assert calls[0][0] == tmp_path / "c"
    assert calls[0][1].project_root == project.root
    assert json.loads(capsys.readouterr().out) == {"documents": 1}


@pytest.mark.parametrize(
    ("flags", "direction"),
    [([], "incoming"), (["--incoming"], "incoming"), (["--outgoing"], "outgoing")],
)
def test_query_direction(
    flags: list[str], direction: str, monkeypatch: pytest.MonkeyPatch, project: ProjectPaths
) -> None:
    calls: list[tuple[str, str, str]] = []

    def query_links(settings: Settings, snapshot: str, url: str, how: str) -> list[object]:
        calls.append((snapshot, url, how))
        return []

    monkeypatch.setattr(query, "query_links", query_links)

    assert main(["corpus", "query", "--snapshot", "s", "--url", "https://a.test/", *flags]) == 0
    assert calls == [("s", "https://a.test/", direction)]


def test_publish_defaults_to_the_project_kaggle_config(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path, bool, str | None]] = []

    def publish_corpus(
        corpus: Path, config: Path, *, update: bool, message: str | None
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

    def pull_corpus(lock: Path, destination: Path) -> str:
        calls.append((lock, destination))
        return "ok"

    monkeypatch.setattr(kaggle, "pull_corpus", pull_corpus)

    assert main(["corpus", "pull"]) == 0
    assert calls == [(project.corpus_lock, project.corpus_pulled)]
