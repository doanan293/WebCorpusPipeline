from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

from data_pipeline.application.enrich.ports import (
    DownloadFiles,
    Downloads,
    Failures,
    HttpTransport,
    TransportFactory,
)
from data_pipeline.domain.enrich.download_rules import (
    MAX_RETRY_WAIT_SECONDS,
    PermanentDownloadError,
    RetryableDownloadError,
    is_strong_etag,
    looks_like_document,
    raise_for_status,
    range_start,
)
from data_pipeline.domain.enrich.models import DownloadJob, HttpResponse
from data_pipeline.domain.parser.models import DownloadedFile
from data_pipeline.domain.shared.config import Settings


async def _settle_resume(
    response: HttpResponse,
    job: DownloadJob,
    transport: HttpTransport,
    files: DownloadFiles,
    offset: int,
) -> tuple[HttpResponse, int, str | None]:
    """Keep a resumed body only if the server honoured the range for the same validator.

    Otherwise restart from zero with a plain request. Returns the response to read, the
    offset it continues from and its ETag.
    """
    etag = response.headers.get("ETag")
    if offset and response.status != HTTPStatus.PARTIAL_CONTENT:
        offset = 0
        files.truncate(job)
    if offset and (
        range_start(response.headers.get("Content-Range")) != offset
        or not is_strong_etag(etag)
        or etag != job.strong_etag
    ):
        offset = 0
        files.truncate(job)
        await response.close()
        response = await transport.get(job.url)
        if response.status != HTTPStatus.OK:
            await response.close()
            msg = "validator changed while resuming"
            raise RetryableDownloadError(msg)
        etag = response.headers.get("ETag")
    return response, offset, etag


async def download_one(
    job: DownloadJob,
    downloads: Downloads,
    settings: Settings,
    transport: HttpTransport,
    files: DownloadFiles,
    *,
    files_root: Path,
) -> DownloadedFile:
    offset, headers = files.resume_point(job)
    response = await transport.get(job.url, headers)
    try:
        raise_for_status(response.status, response.headers)
        response, offset, etag = await _settle_resume(response, job, transport, files, offset)
        total = response.headers.get("Content-Length")
        total_int = int(total) + offset if total and total.isdigit() else None
        if total_int and total_int > settings.max_file_bytes:
            msg = "file exceeds max_file_bytes"
            raise PermanentDownloadError(msg)
        job.strong_etag = etag if is_strong_etag(etag) else None
        job.final_url = response.final_url
        job.expected_size = total_int
        await files.write_body(
            job,
            response.body,
            offset,
            max_bytes=settings.max_file_bytes,
            checkpoint_bytes=settings.checkpoint_bytes,
            checkpoint=downloads.checkpoint_download,
        )
    finally:
        await response.close()
    if job.expected_size and files.size(job) != job.expected_size:
        msg = "truncated response"
        raise RetryableDownloadError(msg)
    mime = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].lower()
    if not looks_like_document(files.head(job), mime):
        msg = "response is not a document"
        raise PermanentDownloadError(msg)
    result = files.finalize(job, mime, files_root, before_move=downloads.mark_finalizing)
    downloads.mark_downloaded(result)
    return result


async def run_downloads(
    downloads: Downloads,
    failures: Failures,
    settings: Settings,
    *,
    files_root: Path,
    transport_factory: TransportFactory,
    files: DownloadFiles,
) -> None:
    host_locks = defaultdict(lambda: asyncio.Semaphore(settings.per_host))
    host_last = defaultdict(float)
    active: dict[asyncio.Task[DownloadedFile], DownloadJob] = {}

    async def worker(job: DownloadJob, transport: HttpTransport) -> DownloadedFile:
        host = urlsplit(job.url).netloc.lower()
        async with host_locks[host]:
            wait = settings.host_delay_seconds - (time.monotonic() - host_last[host])
            if wait > 0:
                await asyncio.sleep(wait)
            host_last[host] = time.monotonic()
            return await download_one(
                job, downloads, settings, transport, files, files_root=files_root
            )

    async with transport_factory(settings) as transport:
        while True:
            while len(active) < settings.concurrency:
                job = downloads.claim_download()
                if not job:
                    break
                active[asyncio.create_task(worker(job, transport))] = job
            if not active:
                delay = downloads.next_retry_delay()
                if delay is None or delay > MAX_RETRY_WAIT_SECONDS:
                    break
                await asyncio.sleep(min(delay, 2.0))
                continue
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                _record_outcome(failures, settings, active.pop(task), task)


def _record_outcome(
    failures: Failures, settings: Settings, job: DownloadJob, task: asyncio.Task[DownloadedFile]
) -> None:
    """Record a failed download once if it is permanent, else schedule a retry within budget."""
    try:
        task.result()
    except PermanentDownloadError as exc:
        failures.record_failure(job.url, "download", str(exc))
    except Exception as exc:  # noqa: BLE001 -- any other download failure is retried per policy
        if job.attempts > settings.retries:
            failures.record_failure(job.url, "download", str(exc))
            return
        wait = exc.retry_after if isinstance(exc, RetryableDownloadError) else None
        failures.record_failure(
            job.url, "download", str(exc), time.time() + (wait or 2**job.attempts)
        )
