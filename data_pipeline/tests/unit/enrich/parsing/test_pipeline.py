from __future__ import annotations

import threading
from pathlib import Path

import pytest

from data_pipeline.core.settings import Settings
from data_pipeline.enrich.models import DownloadedFile, ExtractedPart, LocalResult, ParseResult
from data_pipeline.enrich.parsing import pipeline
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool
from data_pipeline.enrich.state import WorkState


def test_cloud_parses_run_concurrently_up_to_cloud_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workers = 3
    files = [
        DownloadedFile(
            f"https://a.test/{i}.pdf", tmp_path / f"{i}.pdf", f"sha{i}", 1, "application/pdf", ""
        )
        for i in range(workers)
    ]
    together = threading.Barrier(workers, timeout=5)

    def parse_local(file: DownloadedFile) -> LocalResult:
        return LocalResult(
            file.sha256, [ExtractedPart(1, "page", "", True, "pymupdf")], [], "pymupdf"
        )

    def build_pdf_subset(source: Path, pages: list[int], target: Path) -> dict[int, int]:
        return {1: 1}

    def parse_cloud_subset(
        file: Path,
        local: LocalResult,
        mapping: dict[int, int],
        state: WorkState,
        settings: Settings,
        pool: KeyPool,
    ) -> ParseResult:
        state.save_cloud_job(local.source_sha256, str(file), status="accepted")
        together.wait()  # fails unless every file is in the cloud at the same time
        part = ExtractedPart(1, "page", f"cloud {local.source_sha256}", True, "llamaparse-fast")
        return ParseResult(local.source_sha256, [part], "extracted", [], {"llamaparse": "x"})

    monkeypatch.setattr(pipeline, "parse_local", parse_local)

    def cloud_part_indices(local: LocalResult) -> list[int]:
        return [1]

    monkeypatch.setattr(pipeline, "cloud_part_indices", cloud_part_indices)
    monkeypatch.setattr(pipeline, "build_pdf_subset", build_pdf_subset)
    monkeypatch.setattr(pipeline, "parse_cloud_subset", parse_cloud_subset)

    with WorkState.open(tmp_path / "work", "fp") as state:
        monkeypatch.setattr(state, "pending_parse", lambda: iter(files))
        pipeline.extract_documents(
            state, Settings(tmp_path, cloud_workers=workers), api_keys=[("K1", "s")]
        )
        texts = {
            row["url"]: row["result_json"]
            for row in state.conn.execute("SELECT url, result_json FROM parse_results")
        }
        jobs = state.conn.execute("SELECT count(*) FROM cloud_jobs").fetchone()[0]

    assert jobs == workers
    assert all(f"cloud {file.sha256}" in texts[file.url] for file in files)
