from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from ir_data.models import DownloadedFile, ExtractedPart, LocalResult


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def useful_text(value: str) -> bool:
    value = unicodedata.normalize("NFC", value or "")
    return bool(re.search(r"[\w\d]", value, flags=re.UNICODE))


def result(
    file: DownloadedFile, parts: list[ExtractedPart], parser: str, warnings: list[str] | None = None
) -> LocalResult:
    return LocalResult(file.sha256 or file_sha256(file.path), parts, warnings or [], parser)
