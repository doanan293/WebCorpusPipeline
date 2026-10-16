from __future__ import annotations

import hashlib
import os
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from data_pipeline.enrich.domain.download_rules import (
    PermanentDownloadError,
    extension_for,
    is_strong_etag,
)
from data_pipeline.enrich.domain.models import DownloadedFile, DownloadJob


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(file: DownloadedFile) -> bool:
    return (
        file.path.is_file()
        and file.path.stat().st_size == file.size
        and sha256_file(file.path) == file.sha256
    )


class LocalDownloadFiles:
    """Partial downloads in ``parts/``, finished ones content-addressed in ``files/``."""

    def truncate(self, job: DownloadJob) -> None:
        with job.part_path.open("wb"):
            pass

    def resume_point(self, job: DownloadJob) -> tuple[int, dict[str, str]]:
        """Trim the partial file to its durable prefix; return the offset and request headers."""
        job.part_path.parent.mkdir(parents=True, exist_ok=True)
        existing = job.part_path.stat().st_size if job.part_path.exists() else 0
        offset = min(existing, job.durable_offset or existing)
        with job.part_path.open("r+b" if job.part_path.exists() else "wb") as stream:
            stream.truncate(offset)
        headers: dict[str, str] = {}
        if offset and job.strong_etag and is_strong_etag(job.strong_etag):
            headers.update({"Range": f"bytes={offset}-", "If-Range": job.strong_etag})
        elif offset:
            offset = 0
            self.truncate(job)
        return offset, headers

    async def write_body(
        self,
        job: DownloadJob,
        body: AsyncIterator[bytes],
        offset: int,
        *,
        max_bytes: int,
        checkpoint_bytes: int,
        checkpoint: Callable[[DownloadJob], None],
    ) -> int:
        written = offset
        last_flush = written
        last_time = time.monotonic()
        with job.part_path.open("ab") as stream:
            async for chunk in body:
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    msg = "file exceeds max_file_bytes"
                    raise PermanentDownloadError(msg)
                stream.write(chunk)
                if written - last_flush >= checkpoint_bytes or time.monotonic() - last_time >= 1:
                    stream.flush()
                    os.fsync(stream.fileno())
                    job.durable_offset = written
                    checkpoint(job)
                    last_flush, last_time = written, time.monotonic()
            stream.flush()
            os.fsync(stream.fileno())
        job.durable_offset = written
        checkpoint(job)
        return written

    def size(self, job: DownloadJob) -> int:
        return job.part_path.stat().st_size

    def head(self, job: DownloadJob, size: int = 2048) -> bytes:
        with job.part_path.open("rb") as stream:
            return stream.read(size)

    def finalize(
        self,
        job: DownloadJob,
        mime: str,
        files_root: Path,
        *,
        before_move: Callable[[DownloadedFile], None],
    ) -> DownloadedFile:
        """Move the verified part file into the content-addressed ``files`` directory."""
        digest = sha256_file(job.part_path)
        name = hashlib.sha256(job.url.encode()).hexdigest() + extension_for(
            mime, job.final_url or job.url
        )
        final = files_root / name
        final.parent.mkdir(parents=True, exist_ok=True)
        result = DownloadedFile(
            job.url, final, digest, job.part_path.stat().st_size, mime, job.final_url or job.url
        )
        before_move(result)
        job.part_path.replace(final)
        try:
            fd = os.open(final.parent, os.O_DIRECTORY)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass
        return result
