from __future__ import annotations

import pymupdf as fitz

from data_pipeline.domain.parser.cloud_pages import useful_text
from data_pipeline.domain.parser.models import DownloadedFile, ExtractedPart, LocalResult
from data_pipeline.infrastructure.parser.parsers.common import result


def parse_pdf(file: DownloadedFile) -> LocalResult:
    warnings: list[str] = []
    parts: list[ExtractedPart] = []
    with fitz.open(file.path) as document:
        for number, page in enumerate(document, 1):
            text = str(page.get_text("text", sort=True)).strip()
            visual = bool(page.get_images(full=True) or page.get_drawings())
            parts.append(ExtractedPart(number, "page", text, visual, "pymupdf"))
            if not useful_text(text) and visual:
                warnings.append(f"page {number} has visual content but no local text")
    return result(file, parts, f"pymupdf/{fitz.VersionBind}", warnings)
