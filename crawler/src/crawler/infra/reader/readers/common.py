from __future__ import annotations

from crawler.domain.reader.models import DownloadedFile, ExtractedPart, LocalResult


def result(
    file: DownloadedFile, parts: list[ExtractedPart], reader: str, warnings: list[str] | None = None
) -> LocalResult:
    return LocalResult(file.sha256, parts, warnings or [], reader)
