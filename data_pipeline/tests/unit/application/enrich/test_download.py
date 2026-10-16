from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.application.enrich.download import download_one, run_downloads
from data_pipeline.domain.enrich.download_rules import (
    PermanentDownloadError,
    RetryableDownloadError,
)
from data_pipeline.domain.enrich.models import (
    DownloadedFile,
    DownloadJob,
    HttpResponse,
    SourceProgress,
)
from data_pipeline.domain.shared.config import ProjectPaths, Settings
from data_pipeline.infrastructure.enrich.files.download_files import LocalDownloadFiles
from data_pipeline.infrastructure.enrich.http.aiohttp_transport import USER_AGENT
from data_pipeline.infrastructure.enrich.sqlite.work_state import SqliteWorkState
from data_pipeline.infrastructure.shared.settings import load_settings
from tests.fakes.enrich import FakeTransport, Served, noop_close

PDF = b"%PDF-1.4\n" + b"0123456789" * 10
URL = "https://a.test/files/report.pdf"
FILES = LocalDownloadFiles()


async def chunks(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def reply(
    status: int = 200,
    body: bytes = PDF,
    headers: dict[str, str] | None = None,
    url: str = URL,
) -> HttpResponse:
    merged = {
        "Content-Type": "application/pdf",
        "Content-Length": str(len(body)),
        **(headers or {}),
    }
    return HttpResponse(status, merged, url, chunks(body), noop_close)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(ProjectPaths(tmp_path), {"work_dir": tmp_path / "work"})


@pytest.fixture
def state(tmp_path: Path):
    with SqliteWorkState.open(tmp_path / "work", "c1") as opened:
        yield opened


def job_for(state: SqliteWorkState, **fields: Any) -> DownloadJob:
    part = state.root / "parts" / "job.part"
    return DownloadJob(URL, part, **fields)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_downloads_a_document_and_records_it(state: SqliteWorkState, settings: Settings) -> None:
    job = job_for(state)

    result = run(download_one(job, state, settings, FakeTransport(reply()), FILES))

    expected_name = hashlib.sha256(URL.encode()).hexdigest() + ".pdf"
    assert result.path == state.root / "files" / expected_name
    assert result.path.read_bytes() == PDF
    assert result.sha256 == hashlib.sha256(PDF).hexdigest()
    assert result.mime_type == "application/pdf"
    assert not job.part_path.exists()


def test_resumes_with_a_range_request_when_the_etag_is_strong(
    state: SqliteWorkState, settings: Settings
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

    result = run(download_one(job, state, settings, transport, FILES))

    assert transport.requests == [{"Range": "bytes=20-", "If-Range": '"v1"'}]
    assert result.path.read_bytes() == PDF


def test_restarts_from_zero_when_the_server_ignores_the_range(
    state: SqliteWorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])

    result = run(download_one(job, state, settings, FakeTransport(reply(200, PDF)), FILES))

    assert result.path.read_bytes() == PDF


def test_restarts_with_a_plain_request_when_the_validator_changed(
    state: SqliteWorkState, settings: Settings
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

    result = run(download_one(job, state, settings, transport, FILES))

    assert transport.requests == [{"Range": "bytes=20-", "If-Range": '"v1"'}, None]
    assert result.path.read_bytes() == PDF
    assert job.strong_etag == '"v2"'


def test_a_changed_validator_followed_by_a_bad_status_is_retryable(
    state: SqliteWorkState, settings: Settings
) -> None:
    job = job_for(state, durable_offset=20, strong_etag='"v1"')
    job.part_path.parent.mkdir(parents=True)
    job.part_path.write_bytes(PDF[:20])
    transport = FakeTransport(
        reply(206, PDF[20:], {"Content-Range": "bytes 20-99/100", "ETag": '"v2"'}),
        reply(503, b""),
    )

    with pytest.raises(RetryableDownloadError, match="validator changed"):
        run(download_one(job, state, settings, transport, FILES))


@pytest.mark.parametrize("status", [404, 410, 403, 301])
def test_client_errors_are_permanent(
    status: int, state: SqliteWorkState, settings: Settings
) -> None:
    with pytest.raises(PermanentDownloadError, match=f"HTTP {status}"):
        run(download_one(job_for(state), state, settings, FakeTransport(reply(status, b"")), FILES))


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_transient_statuses_are_retryable_and_honour_retry_after(
    status: int, state: SqliteWorkState, settings: Settings
) -> None:
    transport = FakeTransport(reply(status, b"", {"Retry-After": "7"}))

    with pytest.raises(RetryableDownloadError) as caught:
        run(download_one(job_for(state), state, settings, transport, FILES))

    assert str(caught.value) == f"HTTP {status}"
    assert caught.value.retry_after == 7.0


def test_a_declared_size_over_the_limit_is_permanent(
    state: SqliteWorkState, settings: Settings
) -> None:
    big = reply(200, PDF, {"Content-Length": str(settings.max_file_bytes + 1)})

    with pytest.raises(PermanentDownloadError, match="max_file_bytes"):
        run(download_one(job_for(state), state, settings, FakeTransport(big), FILES))


def test_a_body_that_outgrows_the_limit_is_permanent(
    state: SqliteWorkState, settings: Settings, tmp_path: Path
) -> None:
    small = load_settings(
        ProjectPaths(tmp_path), {"work_dir": tmp_path / "work", "max_file_bytes": 50}
    )
    no_length = reply(200, PDF, {"Content-Length": ""})

    with pytest.raises(PermanentDownloadError, match="max_file_bytes"):
        run(download_one(job_for(state), state, small, FakeTransport(no_length), FILES))
    assert small.max_file_bytes == 50 and settings.max_file_bytes > 50


def test_a_short_body_is_a_truncated_response(state: SqliteWorkState, settings: Settings) -> None:
    short = reply(200, PDF[:30], {"Content-Length": str(len(PDF))})

    with pytest.raises(RetryableDownloadError, match="truncated response"):
        run(download_one(job_for(state), state, settings, FakeTransport(short), FILES))


def test_html_pretending_to_be_a_pdf_is_rejected(
    state: SqliteWorkState, settings: Settings
) -> None:
    page = reply(200, b"<html><body>not a pdf</body></html>")

    with pytest.raises(PermanentDownloadError, match="not a document"):
        run(download_one(job_for(state), state, settings, FakeTransport(page), FILES))


def test_run_downloads_downloads_pending_files_and_schedules_retries(
    state: SqliteWorkState, settings: Settings
) -> None:
    good, bad = "https://a.test/good.pdf", "https://b.test/bad.pdf"
    state.add_inventory_batch([good, bad], [], SourceProgress("s", 0, 0, 0, done=True))
    replies = {good: lambda: reply(200, PDF, url=good), bad: lambda: reply(503, b"", url=bad)}

    class Transport:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def __aenter__(self) -> Transport:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
            return replies[url]()

    fast = load_settings(
        ProjectPaths(settings.project_root),
        {"work_dir": settings.work_path, "retries": 1, "host_delay_seconds": 0.001},
    )

    summary = run(run_downloads(state, fast, transport_factory=Transport, files=FILES))

    assert summary.downloaded == 1
    failure = state.conn.execute(
        "SELECT stage, reason FROM failures WHERE url=?", (bad,)
    ).fetchone()
    assert failure is not None
    assert (failure["stage"], failure["reason"]) == ("download", "HTTP 503")


def serve(state: SqliteWorkState, replies: dict[str, Any]) -> Served:
    state.add_inventory_batch(list(replies), [], SourceProgress("s", 0, 0, 0, done=True))
    return Served(replies)


def quick(settings: Settings, **overrides: object) -> Settings:
    return load_settings(
        ProjectPaths(settings.project_root),
        {"work_dir": settings.work_path, "host_delay_seconds": 0.001, **overrides},
    )


def test_permanent_error_is_not_retried(state: SqliteWorkState, settings: Settings) -> None:
    transport = serve(state, {URL: lambda: reply(404, b"", url=URL)})

    run(run_downloads(state, quick(settings, retries=3), transport_factory=transport, files=FILES))

    assert transport.calls == [URL]
    row = state.conn.execute(
        "SELECT status, retry_at FROM inventory WHERE url=?", (URL,)
    ).fetchone()
    assert (row["status"], row["retry_at"]) == ("failed", None)


def test_retry_after_http_date_is_parsed(state: SqliteWorkState, settings: Settings) -> None:
    when = "Wed, 21 Oct 2099 07:28:00 GMT"
    transport = serve(state, {URL: lambda: reply(503, b"", {"Retry-After": when}, url=URL)})

    run(run_downloads(state, quick(settings), transport_factory=transport, files=FILES))

    row = state.conn.execute(
        "SELECT status, retry_at FROM inventory WHERE url=?", (URL,)
    ).fetchone()
    assert row["status"] == "retry"
    assert row["retry_at"] > 4_000_000_000


def test_retry_after_is_capped_at_300_seconds(state: SqliteWorkState, settings: Settings) -> None:
    transport = serve(state, {URL: lambda: reply(429, b"", {"Retry-After": "100000"}, url=URL)})
    started = time.monotonic()

    summary = run(run_downloads(state, quick(settings), transport_factory=transport, files=FILES))

    assert time.monotonic() - started < 5
    assert summary.pending == 1
    assert transport.calls == [URL]


def test_one_session_per_run(state: SqliteWorkState, settings: Settings) -> None:
    urls = [f"https://a.test/{n}.pdf" for n in range(3)]
    transport = serve(state, {url: (lambda u=url: reply(200, PDF, url=u)) for url in urls})

    summary = run(run_downloads(state, quick(settings), transport_factory=transport, files=FILES))

    assert summary.downloaded == 3
    assert transport.sessions == 1


def test_recover_finishes_or_resets_finalizing_rows(
    state: SqliteWorkState, settings: Settings
) -> None:
    good, lost = "https://a.test/good.pdf", "https://a.test/lost.pdf"
    state.add_inventory_batch([good, lost], [], SourceProgress("s", 0, 0, 0, done=True))
    final = state.root / "files" / "good.pdf"
    final.parent.mkdir(parents=True)
    final.write_bytes(PDF)
    digest = hashlib.sha256(PDF).hexdigest()
    for url, path in ((good, final), (lost, state.root / "files" / "lost.pdf")):
        state.mark_finalizing(DownloadedFile(url, path, digest, len(PDF), "application/pdf", url))

    state.recover()

    statuses = dict(state.conn.execute("SELECT url, status FROM inventory").fetchall())
    assert statuses == {good: "downloaded", lost: "pending"}


def test_retry_failed_resets_failed_rows_and_parts(state: SqliteWorkState) -> None:
    state.add_inventory_batch([URL], [], SourceProgress("s", 0, 0, 0, done=True))
    part = state.root / "parts" / "x.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"partial")
    state.conn.execute("UPDATE inventory SET part_path=? WHERE url=?", (str(part), URL))
    state.record_failure(URL, "download", "HTTP 404")

    assert state.reset_failed_downloads() == 1

    row = state.conn.execute(
        "SELECT status, attempts FROM inventory WHERE url=?", (URL,)
    ).fetchone()
    assert (row["status"], row["attempts"]) == ("pending", 0)
    assert not part.exists()
    assert state.conn.execute("SELECT count(*) FROM failures").fetchone()[0] == 0


def test_user_agent_names_package() -> None:
    assert f"data-pipeline/{version('data-pipeline')}" == USER_AGENT
