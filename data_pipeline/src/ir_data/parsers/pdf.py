from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from ..models import DownloadedFile, ExtractedPart, LocalResult
from .common import result, useful_text


def parse_pdf(file: DownloadedFile) -> LocalResult:
    warnings: list[str] = []
    parts: list[ExtractedPart] = []
    with fitz.open(file.path) as document:
        for number, page in enumerate(document, 1):
            text = page.get_text("text", sort=True).strip()
            visual = bool(page.get_images(full=True) or page.get_drawings())
            parts.append(ExtractedPart(number, "page", text, visual, "pymupdf"))
            if not useful_text(text) and visual:
                warnings.append(f"page {number} has visual content but no local text")
    return result(file, parts, f"pymupdf/{fitz.VersionBind}", warnings)


def has_useful_text(text: str) -> bool:
    return useful_text(text)


def cloud_part_indices(local: LocalResult) -> list[int]:
    return [p.index for p in local.parts if not useful_text(p.text) and p.has_visual_content]


def build_pdf_subset(path: Path, indices: list[int], destination: Path) -> dict[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    mapping: dict[int, int] = {}
    with fitz.open(path) as source, fitz.open() as target:
        for new_index, original in enumerate(indices, 1):
            target.insert_pdf(source, from_page=original - 1, to_page=original - 1)
            mapping[new_index] = original
        target.save(destination)
    return mapping
