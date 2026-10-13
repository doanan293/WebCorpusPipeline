from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from data_pipeline.cli.main import main
from data_pipeline.core.paths import ProjectPaths, project_paths
from data_pipeline.core.settings import Settings
from data_pipeline.corpus import package
from data_pipeline.crawl import run as run_package
from data_pipeline.enrich import session as enrich_session
from data_pipeline.enrich.download import downloader
from data_pipeline.enrich.models import RunSummary


class FakeState:
    def __init__(self, summary: RunSummary) -> None:
        self._summary = summary

    def summary(self) -> RunSummary:
        return self._summary


def fake_open_work_state(seen: dict[str, object], summary: RunSummary | None = None) -> object:
    @contextmanager
    def open_work_state(settings: Settings, *, rebind: bool) -> Iterator[FakeState]:
        seen["settings"] = settings
        seen["rebind"] = rebind
        yield FakeState(summary or RunSummary())

    return open_work_state


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["crawl", "--help"],
        ["crawl", "run", "--help"],
        ["crawl", "discover", "--help"],
        ["enrich", "--help"],
        ["enrich", "status", "--help"],
        ["corpus", "--help"],
        ["corpus", "verify", "--help"],
        ["domains", "audit", "--help"],
        ["eval", "extraction", "--help"],
    ],
)
def test_help_for_every_command(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(args) == 0
    assert "Usage" in capsys.readouterr().out


def test_defaults_resolve_from_the_project_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}
    monkeypatch.setattr(enrich_session, "open_work_state", fake_open_work_state(seen))

    assert main(["enrich", "status"]) == 0

    settings = seen["settings"]
    assert isinstance(settings, Settings)
    assert settings.source_path == project_paths().crawl_final
    assert settings.work_path == project_paths().enrichment


def test_relative_paths_typed_by_the_user_follow_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}
    monkeypatch.setattr(enrich_session, "open_work_state", fake_open_work_state(seen))

    assert main(["enrich", "status", "--source", "src-dir", "--work-dir", "work"]) == 0

    settings = seen["settings"]
    assert isinstance(settings, Settings)
    assert settings.source_path == tmp_path / "src-dir"
    assert settings.work_path == tmp_path / "work"


def test_rebind_source_flag_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(enrich_session, "open_work_state", fake_open_work_state(seen))

    assert main(["enrich", "status", "--rebind-source"]) == 0
    assert seen["rebind"] is True


def test_status_with_pending_work_exits_4(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        enrich_session, "open_work_state", fake_open_work_state(seen, RunSummary(pending=3))
    )

    assert main(["enrich", "status"]) == 4


def test_missing_extra_prints_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    project: ProjectPaths,
) -> None:
    monkeypatch.delitem(sys.modules, "data_pipeline.crawl.run.full", raising=False)
    monkeypatch.delattr(run_package, "full", raising=False)
    monkeypatch.setitem(sys.modules, "scrapy.crawler", None)
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")

    code = main(["crawl", "run", "-i", str(seeds), "-o", str(tmp_path / "out")])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.strip() == (
        "Error: this command needs the 'crawl' extra (scrapy is not installed); "
        "run: uv sync --extra crawl"
    )
    assert "Traceback" not in captured.err


def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:

    async def interrupted(state: object, settings: object) -> RunSummary:
        raise KeyboardInterrupt

    monkeypatch.setattr(enrich_session, "open_work_state", fake_open_work_state({}))
    monkeypatch.setattr(downloader, "run_downloads", interrupted)

    assert main(["enrich", "download"]) == 130


def test_business_errors_print_an_error_line_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    def bad(corpus: Path) -> dict[str, object]:
        msg = "bad corpus"
        raise ValueError(msg)

    monkeypatch.setattr(package, "verify_corpus", bad)

    assert main(["corpus", "verify", "--corpus", "x"]) == 2
    assert capsys.readouterr().err.strip() == "Error: bad corpus"
