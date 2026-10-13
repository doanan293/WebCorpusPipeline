from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections import defaultdict
from http import HTTPStatus
from urllib.parse import urlsplit

from data_pipeline.enrich.download.artifacts import extension_for, looks_like_document, sha256_file
from data_pipeline.enrich.download.transport import PublicTransport, Response
from data_pipeline.enrich.models import DownloadedFile, DownloadJob

_RETRYABLE_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_EARLY, HTTPStatus.TOO_MANY_REQUESTS}
)
_SERVER_ERRORS = range(HTTPStatus.INTERNAL_SERVER_ERROR, 600)


class RetryableDownloadError(RuntimeError):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


class PermanentDownloadError(RuntimeError):
    pass


def _strong(value: str | None) -> bool:
    return bool(value and not value.startswith("W/"))


def _range_start(value: str | None) -> int | None:
    if not value or not value.startswith("bytes "):
        return None
    try:
        return int(value.split()[1].split("-")[0])
    except (ValueError, IndexError):
        return None


def _truncate_part(job: DownloadJob) -> None:
    with job.part_path.open("wb"):
        pass


def _resume_point(job: DownloadJob) -> tuple[int, dict[str, str]]:
    """Trim the partial file to its durable prefix; return the offset and request headers."""
    job.part_path.parent.mkdir(parents=True, exist_ok=True)
    existing = job.part_path.stat().st_size if job.part_path.exists() else 0
    offset = min(existing, job.durable_offset or existing)
    with job.part_path.open("r+b" if job.part_path.exists() else "wb") as stream:
        stream.truncate(offset)
    headers: dict[str, str] = {}
    if offset and _strong(job.strong_etag):
        headers.update({"Range": f"bytes={offset}-", "If-Range": job.strong_etag})
    elif offset:
        offset = 0
        _truncate_part(job)
    return offset, headers


def _raise_for_status(response: Response) -> None:
    status = response.status
    if status in _RETRYABLE_STATUSES or status in _SERVER_ERRORS:
        msg = f"HTTP {status}"
        retry_after = float(response.headers.get("Retry-After", "0") or 0) or None
        raise RetryableDownloadError(msg, retry_after)
    if status not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
        msg = f"HTTP {status}"
        raise PermanentDownloadError(msg)


async def _settle_resume(
    response: Response, job: DownloadJob, transport: PublicTransport, offset: int
) -> tuple[Response, int, str | None]:
    """Keep a resumed body only if the server honoured the range for the same validator.

    Otherwise restart from zero with a plain request. Returns the response to read, the
    offset it continues from and its ETag.
    """
    etag = response.headers.get("ETag")
    if offset and response.status != HTTPStatus.PARTIAL_CONTENT:
        offset = 0
        _truncate_part(job)
    if offset and (
        _range_start(response.headers.get("Content-Range")) != offset
        or not _strong(etag)
        or etag != job.strong_etag
    ):
        offset = 0
        _truncate_part(job)
        await response.close()
        response = await transport.get(job.url)
        if response.status != HTTPStatus.OK:
            await response.close()
            msg = "validator changed while resuming"
            raise RetryableDownloadError(msg)
        etag = response.headers.get("ETag")
    return response, offset, etag


async def _write_body(job: DownloadJob, state, settings, response: Response, offset: int) -> None:
    written = offset
    last_flush = written
    last_time = time.monotonic()
    with job.part_path.open("ab") as stream:
        async for chunk in response.body:
            if not chunk:
                continue
            written += len(chunk)
            if written > settings.max_file_bytes:
                msg = "file exceeds max_file_bytes"
                raise PermanentDownloadError(msg)
            stream.write(chunk)
            if (
                written - last_flush >= settings.checkpoint_bytes
                or time.monotonic() - last_time >= 1
            ):
                stream.flush()
                os.fsync(stream.fileno())
                job.durable_offset = written
                state.checkpoint_download(job)
                last_flush, last_time = written, time.monotonic()
        stream.flush()
        os.fsync(stream.fileno())
    job.durable_offset = written
    state.checkpoint_download(job)


def _finalize(job: DownloadJob, state, mime: str) -> DownloadedFile:
    """Move the verified part file into the content-addressed ``files`` directory."""
    digest = sha256_file(job.part_path)
    final = (
        state.root
        / "files"
        / (
            hashlib.sha256(job.url.encode()).hexdigest()
            + extension_for(mime, job.final_url or job.url)
        )
    )
    final.parent.mkdir(parents=True, exist_ok=True)
    result = DownloadedFile(
        job.url, final, digest, job.part_path.stat().st_size, mime, job.final_url or job.url
    )
    state.mark_finalizing(result)
    job.part_path.replace(final)
    try:
        fd = os.open(final.parent, os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass
    state.mark_downloaded(result)
    return result


async def download_one(
    job: DownloadJob, state, settings, transport: PublicTransport
) -> DownloadedFile:
    offset, headers = _resume_point(job)
    response = await transport.get(job.url, headers)
    try:
        _raise_for_status(response)
        response, offset, etag = await _settle_resume(response, job, transport, offset)
        total = response.headers.get("Content-Length")
        total_int = int(total) + offset if total and total.isdigit() else None
        if total_int and total_int > settings.max_file_bytes:
            msg = "file exceeds max_file_bytes"
            raise PermanentDownloadError(msg)
        job.strong_etag = etag if _strong(etag) else None
        job.final_url = response.final_url
        job.expected_size = total_int
        await _write_body(job, state, settings, response, offset)
    finally:
        await response.close()
    if job.expected_size and job.part_path.stat().st_size != job.expected_size:
        msg = "truncated response"
        raise RetryableDownloadError(msg)
    mime = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].lower()
    if not looks_like_document(job.part_path, mime):
        msg = "response is not a document"
        raise PermanentDownloadError(msg)
    return _finalize(job, state, mime)


async def run_downloads(
    state, settings, transport_factory=PublicTransport, stop: asyncio.Event | None = None
):

    stop = stop or asyncio.Event()
    host_locks = defaultdict(lambda: asyncio.Semaphore(settings.per_host))
    host_last = defaultdict(float)
    active: dict[asyncio.Task, DownloadJob] = {}

    async def worker(job):
        host = urlsplit(job.url).netloc.lower()
        async with host_locks[host]:
            wait = settings.host_delay_seconds - (time.monotonic() - host_last[host])
            if wait > 0:
                await asyncio.sleep(wait)
            host_last[host] = time.monotonic()
            async with transport_factory(settings) as transport:
                return await download_one(job, state, settings, transport)

    while not stop.is_set():
        while len(active) < settings.concurrency:
            job = state.claim_download()
            if not job:
                break
            task = asyncio.create_task(worker(job))
            active[task] = job
        if not active:
            delay = state.next_retry_delay()
            if delay is None:
                break
            await asyncio.sleep(min(delay, 2.0))
            continue
        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            job = active.pop(task)
            try:
                task.result()
            except RetryableDownloadError as exc:
                if job.attempts <= settings.retries:
                    state.record_failure(
                        job.url,
                        "download",
                        str(exc),
                        time.time() + (exc.retry_after or 2**job.attempts),
                    )
                else:
                    state.record_failure(job.url, "download", str(exc))
            except Exception as exc:  # noqa: BLE001 -- any download failure is recorded and retried per policy
                if job.attempts <= settings.retries:
                    state.record_failure(
                        job.url, "download", str(exc), time.time() + 2**job.attempts
                    )
                else:
                    state.record_failure(job.url, "download", str(exc))
    for task in active:
        task.cancel()
    return state.summary()
