from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from data_pipeline.application.enrich.extract import extract_documents
from data_pipeline.domain.enrich.key_pool import KeysExhaustedError
from data_pipeline.domain.enrich.models import (
    DownloadedFile,
    ExtractedPart,
    LocalResult,
    ParseResult,
)
from data_pipeline.domain.shared.config import Settings
from data_pipeline.infrastructure.enrich.files.cache import FileParseCache
from data_pipeline.infrastructure.enrich.sqlite.work_state import SqliteWorkState
from tests.fakes.enrich import FakeCloudParser, FakeLocalParser

type Cloud = Callable[[str, DownloadedFile, LocalResult, list[int]], ParseResult]


def textless(file: DownloadedFile) -> LocalResult:
    """One visual page with no local text: it needs the cloud."""
    return LocalResult(file.sha256, [ExtractedPart(1, "page", "", True, "pymupdf")], [], "pymupdf")


def extracted(local: LocalResult) -> ParseResult:
    part = ExtractedPart(1, "page", f"cloud {local.source_sha256}", True, "llamaparse-fast")
    return ParseResult(local.source_sha256, [part], "extracted", [], {"llamaparse": "x"})


PDF_FILES = [
    DownloadedFile(f"https://a.test/{n}.pdf", Path(f"{n}.pdf"), f"sha{n}", 1, "application/pdf", "")
    for n in range(3)
]


def run_extract(
    tmp_path: Path,
    files: list[DownloadedFile],
    cloud: Cloud,
    *,
    keys: list[tuple[str, str]] | None = None,
    local: Callable[[DownloadedFile], LocalResult] = textless,
    settings: Settings | None = None,
) -> dict[str, dict[str, Any]]:
    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        state.pending_parse = lambda: iter(files)  # type: ignore[method-assign]
        summary = extract_documents(
            state,
            settings or Settings(tmp_path),
            api_keys=[("K1", "s")] if keys is None else keys,
            local=FakeLocalParser(local),
            cloud=FakeCloudParser(cloud),
            cache=FileParseCache(tmp_path / "work" / "cache"),
        )
        rows = {
            row["url"]: {**dict(row), "result": json.loads(row["result_json"])}
            for row in state.conn.execute("SELECT * FROM parse_results")
        }
    rows["summary"] = {"cloud_failed": summary.cloud_failed}
    return rows


def test_cloud_parses_run_concurrently_up_to_cloud_workers(tmp_path: Path) -> None:
    workers = 3
    files = [
        DownloadedFile(
            f"https://a.test/{i}.pdf", tmp_path / f"{i}.pdf", f"sha{i}", 1, "application/pdf", ""
        )
        for i in range(workers)
    ]
    together = threading.Barrier(workers, timeout=5)

    def cloud(
        url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        together.wait()  # fails unless every file is in the cloud at the same time
        return extracted(local)

    rows = run_extract(tmp_path, files, cloud, settings=Settings(tmp_path, cloud_workers=workers))

    assert all(f"cloud {file.sha256}" in rows[file.url]["result_json"] for file in files)


def test_failed_cloud_parse_is_not_cached_so_the_next_run_retries(tmp_path: Path) -> None:
    def cloud(*args: Any) -> ParseResult:
        return ParseResult("sha", [], "cloud_failed", ["unknown: Request timed out."], {})

    run_extract(tmp_path, PDF_FILES[:1], cloud)

    assert list((tmp_path / "work" / "cache").glob("*.json")) == []


def test_worker_exception_becomes_cloud_failed(tmp_path: Path) -> None:
    def explode(*args: Any) -> ParseResult:
        msg = "socket closed"
        raise RuntimeError(msg)

    rows = run_extract(tmp_path, PDF_FILES[:1], explode)

    assert rows[PDF_FILES[0].url]["status"] == "cloud_failed"
    assert rows["summary"]["cloud_failed"] == 1


def test_no_keys_marks_quota_blocked_without_crash(tmp_path: Path) -> None:
    called: list[str] = []

    def cloud(*args: Any) -> ParseResult:
        called.append("cloud")
        raise AssertionError

    rows = run_extract(tmp_path, PDF_FILES[:1], cloud, keys=[])

    row = rows[PDF_FILES[0].url]
    assert row["status"] == "quota_blocked"
    assert "no LlamaParse keys" in row["result"]["warnings"]
    assert called == []


def test_quota_exhaustion_mid_run_keeps_finished_results(tmp_path: Path) -> None:
    def cloud(
        url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        if url == PDF_FILES[0].url:
            return extracted(local)
        msg = "all LlamaParse keys are quota exhausted"
        raise KeysExhaustedError(msg)

    rows = run_extract(tmp_path, PDF_FILES[:2], cloud)

    assert rows[PDF_FILES[0].url]["status"] == "extracted"
    assert rows[PDF_FILES[1].url]["status"] == "quota_blocked"
    assert len(list((tmp_path / "work" / "cache").glob("*.json"))) == 1


def test_finished_cloud_result_written_before_local_loop_ends(tmp_path: Path) -> None:
    cache = tmp_path / "work" / "cache"
    seen_cache: list[bool] = []

    def local(file: DownloadedFile) -> LocalResult:
        if file is PDF_FILES[1]:
            deadline = time.monotonic() + 5
            while not list(cache.glob("*.json")) and time.monotonic() < deadline:
                time.sleep(0.01)
            seen_cache.append(bool(list(cache.glob("*.json"))))
            return LocalResult(file.sha256, [ExtractedPart(1, "page", "text", False, "x")], [], "x")
        return LocalResult(file.sha256, [ExtractedPart(1, "page", "", True, "x")], [], "x")

    def cloud(
        url: str, file: DownloadedFile, result: LocalResult, missing: list[int]
    ) -> ParseResult:
        return extracted(result)

    run_extract(tmp_path, PDF_FILES[:2], cloud, local=local)

    assert seen_cache == [True]


def test_same_document_under_two_urls_is_sent_to_the_cloud_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def cloud(
        url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        calls.append(url)
        return extracted(local)

    same = [
        DownloadedFile(
            f"https://a.test/{n}.pdf", Path(f"{n}.pdf"), "same", 1, "application/pdf", ""
        )
        for n in ("a", "b")
    ]

    rows = run_extract(tmp_path, same, cloud)

    assert len(calls) == 1
    assert {rows[file.url]["status"] for file in same} == {"extracted"}


def test_a_cached_cloud_result_is_reused_without_calling_the_cloud(tmp_path: Path) -> None:
    calls: list[str] = []

    def cloud(
        url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        calls.append(url)
        return extracted(local)

    run_extract(tmp_path, PDF_FILES[:1], cloud)
    rows = run_extract(tmp_path, PDF_FILES[:1], cloud)

    assert calls == [PDF_FILES[0].url]
    assert rows[PDF_FILES[0].url]["status"] == "extracted"
