from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import llama_cloud
import pytest

from crawler.domain.reader.key_pool import KeyPool
from crawler.domain.reader.models import ExtractedPart, LocalResult
from crawler.domain.shared.config import Settings
from crawler.infra.reader.llamaparse import parse_cloud_subset

DOC = "https://a.test/report.pdf"


class StatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeCloud:
    """Stands in for llama_cloud.LlamaCloud; `fail` is raised by wait_for_completion."""

    fail: Exception | None = None
    text = "cloud"
    deleted: list[str]
    keys: list[str]

    def __init__(self, api_key: str) -> None:
        FakeCloud.keys.append(api_key)
        self.files = SimpleNamespace(create=self._create, delete=self._delete)
        self.parsing = SimpleNamespace(
            create=self._create_job, wait_for_completion=self._wait, get=self._get
        )

    def _create_job(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(id="job-1")

    def _get(self, *_a: object, **_k: object) -> dict[str, Any]:
        return {"text": {"pages": [{"page_number": 1, "text": FakeCloud.text}]}}

    def _create(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(id="file-1")

    def _delete(self, file_id: str) -> None:
        FakeCloud.deleted.append(file_id)

    def _wait(self, *_a: Any, **_k: Any) -> None:
        if FakeCloud.fail is not None:
            raise FakeCloud.fail


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> type[FakeCloud]:
    FakeCloud.fail = None
    FakeCloud.text = "cloud"
    FakeCloud.deleted = []
    FakeCloud.keys = []
    monkeypatch.setattr(llama_cloud, "LlamaCloud", FakeCloud)
    return FakeCloud


class _JobLog:
    """A ``CloudJobLog`` keeping the latest fields of each request."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def save_cloud_job(self, request_id: str, url: str, **fields: str | None) -> None:
        job = self.jobs.setdefault(request_id, {"url": url})
        job.update({key: value for key, value in fields.items() if value is not None})


def run_subset(tmp_path: Path, pool: KeyPool) -> tuple[Any, list[dict[str, Any]]]:
    local = LocalResult("sha", [ExtractedPart(1, "page", "", True, "pymupdf")], [], "pymupdf")
    log = _JobLog()
    result = parse_cloud_subset(
        DOC, tmp_path / "subset.pdf", local, {1: 1}, log, Settings(tmp_path), pool
    )
    return result, list(log.jobs.values())


def test_cloud_job_records_document_url(tmp_path: Path, cloud: type[FakeCloud]) -> None:
    result, jobs = run_subset(tmp_path, KeyPool([("K1", "s1")]))

    assert result.status == "extracted"
    assert [job["url"] for job in jobs] == [DOC]


def test_uploaded_file_deleted_after_job(tmp_path: Path, cloud: type[FakeCloud]) -> None:
    cloud.fail = RuntimeError("boom")

    run_subset(tmp_path, KeyPool([("K1", "s1")]))

    assert cloud.deleted == ["file-1"]


def test_failed_job_keeps_file_and_job_ids(tmp_path: Path, cloud: type[FakeCloud]) -> None:
    cloud.fail = RuntimeError("boom")

    result, jobs = run_subset(tmp_path, KeyPool([("K1", "s1")]))

    assert result.status == "cloud_failed"
    assert [(job["file_id"], job["job_id"]) for job in jobs] == [("file-1", "job-1")]


def test_per_file_limit_error_does_not_exhaust_keys(tmp_path: Path, cloud: type[FakeCloud]) -> None:
    cloud.fail = StatusError("page limit exceeded for this file", 403)
    pool = KeyPool([("K1", "s1"), ("K2", "s2")])

    result, _ = run_subset(tmp_path, pool)

    assert result.warnings[0].startswith("cloud_rejected: invalid:")
    assert pool.exhausted == set()
    assert cloud.keys == ["s1"]


def test_llamaparse_part_keeps_local_visual_flag(tmp_path: Path, cloud: type[FakeCloud]) -> None:
    cloud.text = ""

    result, _ = run_subset(tmp_path, KeyPool([("K1", "s1")]))

    assert [(part.text, part.has_visual_content) for part in result.parts] == [("", True)]


def test_rejected_file_keeps_local_result_and_is_not_retried(
    tmp_path: Path, cloud: type[FakeCloud]
) -> None:
    cloud.fail = StatusError("unsupported or encrypted PDF", 422)

    result, _ = run_subset(tmp_path, KeyPool([("K1", "s1")]))

    assert result.status == "extracted"
    assert result.parts == []
    assert result.warnings == ["cloud_rejected: invalid: unsupported or encrypted PDF"]
