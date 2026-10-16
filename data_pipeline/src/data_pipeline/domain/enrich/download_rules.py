from __future__ import annotations

import mimetypes
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from pathlib import PurePosixPath


def extension_for(mime: str, url: str) -> str:
    suffix = PurePosixPath(url.split("?", 1)[0]).suffix.lower()
    known = {
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".xls",
        ".xlsx",
        ".ods",
        ".ppt",
        ".pptx",
        ".odp",
        ".txt",
        ".csv",
        ".rtf",
    }
    if suffix in known:
        return suffix
    normalized = mime.split(";", 1)[0].strip().lower()
    by_mime = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.oasis.opendocument.spreadsheet": ".ods",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.oasis.opendocument.presentation": ".odp",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/rtf": ".rtf",
    }
    return by_mime.get(normalized) or mimetypes.guess_extension(normalized) or ".bin"


def looks_like_document(head: bytes, mime: str) -> bool:
    """Sniff the first bytes of a body: HTML is never a document."""
    data = head[:16]
    lower = mime.lower()
    if b"<html" in data.lower() or b"<!doctype" in data.lower():
        return False
    return (
        data.startswith(b"%PDF")
        or data.startswith(b"PK")
        or data.startswith(b"\xd0\xcf\x11\xe0")
        or lower.startswith(("application/", "text/csv", "text/plain"))
    )


_RETRYABLE_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_EARLY, HTTPStatus.TOO_MANY_REQUESTS}
)
_SERVER_ERRORS = range(HTTPStatus.INTERNAL_SERVER_ERROR, 600)
# Waits longer than this end the run; the row keeps its retry time for a later run.
MAX_RETRY_WAIT_SECONDS = 300.0


class RetryableDownloadError(RuntimeError):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


class PermanentDownloadError(RuntimeError):
    pass


def is_strong_etag(value: str | None) -> bool:
    return bool(value and not value.startswith("W/"))


def range_start(value: str | None) -> int | None:
    if not value or not value.startswith("bytes "):
        return None
    try:
        return int(value.split()[1].split("-")[0])
    except (ValueError, IndexError):
        return None


def retry_after_seconds(value: str | None, now: float) -> float | None:
    """Read Retry-After as seconds or as an HTTP date; None when absent or unreadable."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - now)


def raise_for_status(status: int, headers: Mapping[str, str]) -> None:
    if status in _RETRYABLE_STATUSES or status in _SERVER_ERRORS:
        msg = f"HTTP {status}"
        retry_after = retry_after_seconds(headers.get("Retry-After"), time.time())
        raise RetryableDownloadError(msg, retry_after)
    if status not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
        msg = f"HTTP {status}"
        raise PermanentDownloadError(msg)
