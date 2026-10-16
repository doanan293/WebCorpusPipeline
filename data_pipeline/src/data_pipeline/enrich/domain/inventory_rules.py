from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urldefrag, urlsplit

from data_pipeline.enrich.domain.models import SourceRef
from data_pipeline.shared.domain.refs import classify_ref

DOC_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".csv",
}


def clean_url(value: str) -> str:
    return urldefrag(value.strip())[0]


def is_document_url(url: str) -> bool:
    return PurePosixPath(urlsplit(url).path.lower()).suffix in DOC_EXT


def page_refs(row: dict[str, Any], page: str) -> list[SourceRef]:
    """Return the links and assets of one page that point at a document candidate."""
    refs: list[SourceRef] = []
    for link in row.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            target = clean_url(str(link["url"]))
            if is_document_url(target):
                relation = classify_ref(target, role=None, kind=None)
                refs.append(SourceRef(page, target, relation, str(link.get("text") or "")))
    for asset in row.get("assets") or []:
        if isinstance(asset, dict) and asset.get("url"):
            target = clean_url(str(asset["url"]))
            if is_document_url(target):
                refs.append(
                    SourceRef(
                        page,
                        target,
                        classify_ref(target, role=str(asset.get("role") or "asset"), kind=None),
                        "",
                        str(asset.get("alt") or ""),
                        str(asset.get("caption") or ""),
                    )
                )
    return refs
