from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from data_pipeline.application.enrich import download as downloader
from data_pipeline.application.enrich import export as enrich_export
from data_pipeline.application.enrich import extract as pipeline
from data_pipeline.application.enrich import inventory as enrich_inventory
from data_pipeline.application.enrich import session as enrich_session
from data_pipeline.cli.main import main
from data_pipeline.domain.enrich.models import InventorySummary, RunSummary
from data_pipeline.domain.shared.config import ProjectPaths, Settings
from data_pipeline.infrastructure.enrich.sqlite.work_state import SqliteWorkState
from tests.fakes.enrich import FakeWorkState


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> FakeWorkState:
    fake = FakeWorkState()

    @contextmanager
    def open_work_state(
        settings: Settings, *, rebind: bool, **_: object
    ) -> Iterator[FakeWorkState]:
        yield fake

    monkeypatch.setattr(enrich_session, "open_work_state", open_work_state)
    return fake


def test_status_prints_a_one_line_summary(
    state: FakeWorkState, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["enrich", "status"]) == 0
    assert (
        capsys.readouterr().out.strip()
        == "downloaded=2 parsed=1 failed=0 pending=0 quota_blocked=0 cloud_failed=0"
    )


def test_status_json_prints_the_summary(
    state: FakeWorkState, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["enrich", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["downloaded"] == 2


def test_inventory_indexes_the_source(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Path] = []

    def index(source: Path, current: object) -> InventorySummary:
        calls.append(source)
        return InventorySummary(candidates=5, referrers=7)

    monkeypatch.setattr(enrich_inventory, "index_inventory", index)

    assert main(["enrich", "inventory"]) == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["candidates"] == 5


def test_run_indexes_downloads_and_extracts_in_order(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch
) -> None:

    order: list[str] = []

    def index(source: Path, current: object) -> InventorySummary:
        order.append("inventory")
        return InventorySummary(candidates=0, referrers=0)

    async def download(current: object, settings: object, **_: object) -> RunSummary:
        order.append("download")
        return RunSummary()

    def extract(current: object, settings: object, *, api_keys: object, **_: object) -> RunSummary:
        order.append("extract")
        return RunSummary()

    monkeypatch.setattr(enrich_inventory, "index_inventory", index)
    monkeypatch.setattr(downloader, "run_downloads", download)
    monkeypatch.setattr(pipeline, "extract_documents", extract)

    assert main(["enrich", "run"]) == 0
    assert order == ["inventory", "download", "extract"]


def test_run_skips_inventory_when_it_is_already_done(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch
) -> None:

    state.inventory_is_done = True
    order: list[str] = []

    async def download(current: object, settings: object, **_: object) -> RunSummary:
        order.append("download")
        return RunSummary()

    def extract(current: object, settings: object, *, api_keys: object, **_: object) -> RunSummary:
        order.append("extract")
        return RunSummary()

    monkeypatch.setattr(downloader, "run_downloads", download)
    monkeypatch.setattr(pipeline, "extract_documents", extract)

    assert main(["enrich", "run"]) == 0
    assert order == ["download", "extract"]


def test_export_requires_an_output_directory(state: FakeWorkState) -> None:
    assert main(["enrich", "export"]) == 2


def test_export_writes_to_the_output_directory(
    state: FakeWorkState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def export(current: object, destination: Path) -> Path:
        calls.append(destination)
        return destination

    monkeypatch.setattr(enrich_export, "export_enrichment", export)

    assert main(["enrich", "export", "--output", str(tmp_path / "out")]) == 0
    assert calls == [tmp_path / "out"]


def test_reconcile_job_reports_the_job_status(
    project: ProjectPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    with SqliteWorkState.open(work, "c1") as current:
        current.save_cloud_job(
            "req-1", "https://a.test/f.pdf", file_id="file-1", job_id="job-1", status="running"
        )

    code = main(
        [
            "enrich",
            "reconcile-job",
            "--work-dir",
            str(work),
            "--request-id",
            "req-1",
            "--job-id",
            "job-1",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "request_id": "req-1",
        "job_id": "job-1",
        "status": "running",
    }


def test_reconcile_job_rejects_an_unknown_pair(
    project: ProjectPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    with SqliteWorkState.open(work, "c1"):
        pass

    code = main(
        ["enrich", "reconcile-job", "--work-dir", str(work), "--request-id", "r", "--job-id", "j"]
    )

    assert code == 2
    assert "request/job pair is not present" in capsys.readouterr().err


@pytest.mark.parametrize(("args", "requeued"), [([], False), (["--reparse-cloud"], True)])
def test_extract_reparses_cloud_documents_only_on_request(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch, args: list[str], requeued: bool
) -> None:
    seen: list[bool] = []

    def extract(
        current: FakeWorkState, settings: object, *, api_keys: object, **_: object
    ) -> RunSummary:
        seen.append(current.requeued)
        return current.summary()

    monkeypatch.setattr(pipeline, "extract_documents", extract)

    assert main(["enrich", "extract", *args]) == 0
    assert seen == [requeued]


def test_download_retry_failed_requeues_before_downloading(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []

    def reset() -> int:
        order.append("reset")
        return 2

    async def download(current: object, settings: object, **_: object) -> RunSummary:
        order.append("download")
        return RunSummary()

    monkeypatch.setattr(state, "reset_failed_downloads", reset, raising=False)
    monkeypatch.setattr(downloader, "run_downloads", download)

    assert main(["enrich", "download", "--retry-failed"]) == 0
    assert order == ["reset", "download"]
    assert "requeued 2 failed downloads" in capsys.readouterr().out


def test_cloud_failed_makes_exit_code_4(
    state: FakeWorkState, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(state, "summary", lambda: RunSummary(parsed=1, cloud_failed=2))

    assert main(["enrich", "status"]) == 4
    assert "cloud_failed=2" in capsys.readouterr().out
