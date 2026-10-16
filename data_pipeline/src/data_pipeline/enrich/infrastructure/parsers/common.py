from __future__ import annotations

from data_pipeline.enrich.domain.models import DownloadedFile, ExtractedPart, LocalResult


def result(
    file: DownloadedFile, parts: list[ExtractedPart], parser: str, warnings: list[str] | None = None
) -> LocalResult:
    return LocalResult(file.sha256, parts, warnings or [], parser)
