from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from data_pipeline.core.paths import ProjectPaths
from data_pipeline.core.settings import Settings, load_settings
from data_pipeline.enrich.download.downloader import (
    PermanentDownloadError,
    RetryableDownloadError,
    download_one,
    run_downloads,
)
from data_pipeline.enrich.download.transport import Response
from data_pipeline.enrich.models import Candidate, DownloadJob
from data_pipeline.enrich.state import WorkState

PDF = b"%PDF-1.4\n" + b"0123456789" * 10
URL = "https://a.test/files/report.pdf"


async def chunks(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def reply(
    status: int = 200,
    body: bytes = PDF,
    headers: dict[str, str] | None = None,
    url: str = URL,
) -> Response:
    merged = {"Content-Type": "application/pdf", "Content-Length": str(len(body)), **(headers or {})}
    return Response(status, merged, url, chunks(body), SimpleNamespace(release=lambda: None))


class FakeTransport:
    """Hands out queued replies and records the headers each request carried."""

    def __init__(self, *replies: Response) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, str] | None] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        self.requests.append(headers)
        return self.replies.pop(0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(ProjectPaths(tmp_path), {"work_dir": tmp_path / "work"})


@pytest.fixture
def state(tmp_path: Path):
    with WorkState.open(tmp_path / "work", "fingerprint") as opened:
        yield opened


def job_for(state: WorkState, **fields: Any) -> DownloadJob:
    part = state.root / "parts" / "job.part"
    return DownloadJob(URL, part, **fields)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_downloads_a_document_and_records_it(state: WorkState, settings: Settings) -> None:
    job = job_for(state)

    result = run(download_one(job, state, settings, FakeTransport(reply())))

    expected_name = hashlib.sha256(URL.encode()).hexdigest() + ".pdf"
    assert result.path == state.root / "files" / expected_name
    assert result.path.read_bytes() == PDF
    assert result.sha256 == hashlib.sha256(PDF).hexdigest()
    assert result.mime_type == "application/pdf"
    assert not job.part_path.exists()


def test_resumes_with_a_range_request_when_the_etag_is_strong(
    state: WorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])
    transport = FakeTransport(
        reply(
            206,
            PDF[20:],
            {"Content-Range": f"bytes 20-{len(PDF) - 1}/{len(PDF)}", "ETag": '"v1"'},
        )
    )

    result = run(download_one(job, state, settings, transport))

    assert transport.requests == [{"Range": "bytes=20-", "If-Range": '"v1"'}]
    assert result.path.read_bytes() == PDF


def test_restarts_from_zero_when_the_server_ignores_the_range(
    state: WorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])

    result = run(download_one(job, state, settings, FakeTransport(reply(200, PDF))))

    assert result.path.read_bytes() == PDF


def test_restarts_with_a_plain_request_when_the_validator_changed(
    state: WorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])
    transport = FakeTransport(
        reply(
            206,
            PDF[20:],
            {"Content-Range": f"bytes 20-{len(PDF) - 1}/{len(PDF)}", "ETag": '"v2"'},
        ),
        reply(200, PDF, {"ETag": '"v2"'}),
    )

    result = run(download_one(job, state, settings, transport))

    assert transport.requests == [{"Range": "bytes=20-", "If-Range": '"v1"'}, None]
    assert result.path.read_bytes() == PDF
    assert job.strong_etag == '"v2"'


def test_a_changed_validator_followed_by_a_bad_status_is_retryable(
    state: WorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])
    transport = FakeTransport(
        reply(206, PDF[20:], {"Content-Range": "bytes 20-99/100", "ETag": '"v2"'}),
        reply(503, b""),
    )

    with pytest.raises(RetryableDownloadError, match="validator changed"):
        run(download_one(job, state, settings, transport))


@pytest.mark.parametrize("status", [404, 410, 403, 301])
def test_client_errors_are_permanent(status: int, state: WorkState, settings: Settings) -> None:
    with pytest.raises(PermanentDownloadError, match=f"HTTP {status}"):
        run(download_one(job_for(state), state, settings, FakeTransport(reply(status, b""))))


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_transient_statuses_are_retryable_and_honour_retry_after(
    status: int, state: WorkState, settings: Settings
) -> None:
    transport = FakeTransport(reply(status, b"", {"Retry-After": "7"}))

    with pytest.raises(RetryableDownloadError) as caught:
        run(download_one(job_for(state), state, settings, transport))

    assert str(caught.value) == f"HTTP {status}"
    assert caught.value.retry_after == 7.0


def test_a_declared_size_over_the_limit_is_permanent(state: WorkState, settings: Settings) -> None:
    big = reply(200, PDF, {"Content-Length": str(settings.max_file_bytes + 1)})

    with pytest.raises(PermanentDownloadError, match="max_file_bytes"):
        run(download_one(job_for(state), state, settings, FakeTransport(big)))


def test_a_body_that_outgrows_the_limit_is_permanent(
    state: WorkState, settings: Settings, tmp_path: Path
) -> None:
    small = load_settings(
        ProjectPaths(tmp_path), {"work_dir": tmp_path / "work", "max_file_bytes": 50}
    )
    no_length = reply(200, PDF, {"Content-Length": ""})

    with pytest.raises(PermanentDownloadError, match="max_file_bytes"):
        run(download_one(job_for(state), state, small, FakeTransport(no_length)))
    assert small.max_file_bytes == 50 and settings.max_file_bytes > 50


def test_a_short_body_is_a_truncated_response(state: WorkState, settings: Settings) -> None:
    short = reply(200, PDF[:30], {"Content-Length": str(len(PDF))})

    with pytest.raises(RetryableDownloadError, match="truncated response"):
        run(download_one(job_for(state), state, settings, FakeTransport(short)))


def test_html_pretending_to_be_a_pdf_is_rejected(state: WorkState, settings: Settings) -> None:
    page = reply(200, b"<html><body>not a pdf</body></html>")

    with pytest.raises(PermanentDownloadError, match="not a document"):
        run(download_one(job_for(state), state, settings, FakeTransport(page)))


def test_run_downloads_downloads_pending_files_and_schedules_retries(
    state: WorkState, settings: Settings
) -> None:
    good, bad = "https://a.test/good.pdf", "https://b.test/bad.pdf"
    state.add_inventory_batch([Candidate(good), Candidate(bad)], [], 0, done=True, source_key="s")
    replies = {good: lambda: reply(200, PDF, url=good), bad: lambda: reply(503, b"", url=bad)}

    class Transport:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def __aenter__(self) -> Transport:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
            return replies[url]()

    fast = load_settings(
        ProjectPaths(settings.project_root),
        {"work_dir": settings.work_path, "retries": 1, "host_delay_seconds": 0.001},
    )

    summary = run(run_downloads(state, fast, transport_factory=Transport, stop=asyncio.Event()))

    assert summary.downloaded == 1
    failure = state.conn.execute("SELECT stage, reason FROM failures WHERE url=?", (bad,)).fetchone()
    assert failure is not None
    assert (failure["stage"], failure["reason"]) == ("download", "HTTP 503")
