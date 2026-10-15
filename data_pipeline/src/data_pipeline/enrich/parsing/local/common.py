from __future__ import annotations

import re
import unicodedata

from data_pipeline.enrich.models import DownloadedFile, ExtractedPart, LocalResult


def useful_text(value: str) -> bool:
    value = unicodedata.normalize("NFC", value or "")
    return bool(re.search(r"[\w\d]", value, flags=re.UNICODE))


def result(
    file: DownloadedFile, parts: list[ExtractedPart], parser: str, warnings: list[str] | None = None
) -> LocalResult:
    return LocalResult(file.sha256, parts, warnings or [], parser)
