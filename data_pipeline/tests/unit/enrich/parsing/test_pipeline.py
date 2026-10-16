from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.enrich.models import DownloadedFile, ExtractedPart, LocalResult, ParseResult
from data_pipeline.enrich.parsing import pipeline
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool, KeysExhaustedError
from data_pipeline.enrich.state import WorkState
from data_pipeline.shared.domain.config import Settings


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
        document_url: str,
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

    with WorkState.open(tmp_path / "work", "c1") as state:
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


def test_failed_cloud_parse_is_not_cached_so_the_next_run_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = DownloadedFile(
        "https://a.test/x.pdf", tmp_path / "x.pdf", "sha", 1, "application/pdf", ""
    )

    def parse_local(file: DownloadedFile) -> LocalResult:
        return LocalResult(
            file.sha256, [ExtractedPart(1, "page", "", True, "pymupdf")], [], "pymupdf"
        )

    def cloud_part_indices(local: LocalResult) -> list[int]:
        return [1]

    def build_pdf_subset(source: Path, pages: list[int], target: Path) -> dict[int, int]:
        return {1: 1}

    def parse_cloud_subset(*args: object) -> ParseResult:
        return ParseResult("sha", [], "cloud_failed", ["unknown: Request timed out."], {})

    monkeypatch.setattr(pipeline, "parse_local", parse_local)
    monkeypatch.setattr(pipeline, "cloud_part_indices", cloud_part_indices)
    monkeypatch.setattr(pipeline, "build_pdf_subset", build_pdf_subset)
    monkeypatch.setattr(pipeline, "parse_cloud_subset", parse_cloud_subset)

    with WorkState.open(tmp_path / "work", "c1") as state:
        monkeypatch.setattr(state, "pending_parse", lambda: iter([file]))
        pipeline.extract_documents(state, Settings(tmp_path), api_keys=[("K1", "s")])

    assert list((tmp_path / "work" / "cache").glob("*.json")) == []


PDF_FILES = [
    DownloadedFile(f"https://a.test/{n}.pdf", Path(f"{n}.pdf"), f"sha{n}", 1, "application/pdf", "")
    for n in range(3)
]


def cloud_only(monkeypatch: pytest.MonkeyPatch, cloud: Any) -> None:
    """Every page needs the cloud; `cloud` stands in for parse_cloud_subset."""

    def parse_local(file: DownloadedFile) -> LocalResult:
        return LocalResult(
            file.sha256, [ExtractedPart(1, "page", "", True, "pymupdf")], [], "pymupdf"
        )

    def subset(source: Path, pages: list[int], target: Path) -> dict[int, int]:
        return {1: 1}

    monkeypatch.setattr(pipeline, "parse_local", parse_local)

    def every_page(local: LocalResult) -> list[int]:
        return [1]

    monkeypatch.setattr(pipeline, "cloud_part_indices", every_page)
    monkeypatch.setattr(pipeline, "build_pdf_subset", subset)
    monkeypatch.setattr(pipeline, "parse_cloud_subset", cloud)


def extracted(local: LocalResult) -> ParseResult:
    part = ExtractedPart(1, "page", f"cloud {local.source_sha256}", True, "llamaparse-fast")
    return ParseResult(local.source_sha256, [part], "extracted", [], {"llamaparse": "x"})


def run_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: list[DownloadedFile], keys: Any
) -> dict[str, dict[str, Any]]:
    with WorkState.open(tmp_path / "work", "c1") as state:
        monkeypatch.setattr(state, "pending_parse", lambda: iter(files))
        summary = pipeline.extract_documents(state, Settings(tmp_path), api_keys=keys)
        rows = {
            row["url"]: {**dict(row), "result": json.loads(row["result_json"])}
            for row in state.conn.execute("SELECT * FROM parse_results")
        }
    rows["summary"] = {"cloud_failed": summary.cloud_failed}
    return rows


def test_worker_exception_becomes_cloud_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object) -> ParseResult:
        msg = "socket closed"
        raise RuntimeError(msg)

    cloud_only(monkeypatch, explode)

    rows = run_extract(tmp_path, monkeypatch, PDF_FILES[:1], [("K1", "s")])

    assert rows[PDF_FILES[0].url]["status"] == "cloud_failed"
    assert rows["summary"]["cloud_failed"] == 1


def test_no_keys_marks_quota_blocked_without_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def cloud(*args: object) -> ParseResult:
        called.append("cloud")
        raise AssertionError

    cloud_only(monkeypatch, cloud)

    rows = run_extract(tmp_path, monkeypatch, PDF_FILES[:1], [])

    row = rows[PDF_FILES[0].url]
    assert row["status"] == "quota_blocked"
    assert "no LlamaParse keys" in row["result"]["warnings"]
    assert called == []


def test_quota_exhaustion_mid_run_keeps_finished_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cloud(document_url: str, file: Path, local: LocalResult, *rest: object) -> ParseResult:
        if document_url == PDF_FILES[0].url:
            return extracted(local)
        msg = "all LlamaParse keys are quota exhausted"
        raise KeysExhaustedError(msg)

    cloud_only(monkeypatch, cloud)

    rows = run_extract(tmp_path, monkeypatch, PDF_FILES[:2], [("K1", "s")])

    assert rows[PDF_FILES[0].url]["status"] == "extracted"
    assert rows[PDF_FILES[1].url]["status"] == "quota_blocked"
    assert len(list((tmp_path / "work" / "cache").glob("*.json"))) == 1


def test_finished_cloud_result_written_before_local_loop_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "work" / "cache"
    seen_cache: list[bool] = []

    def parse_local(file: DownloadedFile) -> LocalResult:
        if file is PDF_FILES[1]:
            deadline = time.monotonic() + 5
            while not list(cache.glob("*.json")) and time.monotonic() < deadline:
                time.sleep(0.01)
            seen_cache.append(bool(list(cache.glob("*.json"))))
            return LocalResult(file.sha256, [ExtractedPart(1, "page", "text", False, "x")], [], "x")
        return LocalResult(file.sha256, [ExtractedPart(1, "page", "", True, "x")], [], "x")

    def cloud(url: str, file: Path, local: LocalResult, *rest: object) -> ParseResult:
        return extracted(local)

    def textless_pages(local: LocalResult) -> list[int]:
        return [] if local.parts[0].text else [1]

    cloud_only(monkeypatch, cloud)
    monkeypatch.setattr(pipeline, "parse_local", parse_local)
    monkeypatch.setattr(pipeline, "cloud_part_indices", textless_pages)

    run_extract(tmp_path, monkeypatch, PDF_FILES[:2], [("K1", "s")])

    assert seen_cache == [True]


def test_same_document_under_two_urls_is_sent_to_the_cloud_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def cloud(url: str, file: Path, local: LocalResult, *rest: object) -> ParseResult:
        calls.append(url)
        return extracted(local)

    cloud_only(monkeypatch, cloud)
    same = [
        DownloadedFile(
            f"https://a.test/{n}.pdf", Path(f"{n}.pdf"), "same", 1, "application/pdf", ""
        )
        for n in ("a", "b")
    ]

    rows = run_extract(tmp_path, monkeypatch, same, [("K1", "s")])

    assert len(calls) == 1
    assert {rows[file.url]["status"] for file in same} == {"extracted"}
