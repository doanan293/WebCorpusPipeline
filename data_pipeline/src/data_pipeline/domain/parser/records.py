"""The records the reader block sends on: the slide's ``/add`` fields plus what extraction knows."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from data_pipeline.domain.parser.extract.article import ArticleAnalysis

_EXTRA_FIELDS = ("published_at", "modified_at", "author", "language", "headings", "content_blocks")
_MIME_FORMATS = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
_MAX_SUFFIX = 5


def content_id_for(text: str) -> str:
    """The content hash stored for extracted text (same as the batch crawl used)."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def record_from_article(url: str, article: Mapping[str, Any]) -> dict[str, Any]:
    """An HTML record from an extracted article dict (fresh, or stored by the batch crawl)."""
    text = str(article.get("text") or "")
    record: dict[str, Any] = {
        "url": url,
        "format": "html",
        "title": str(article.get("title") or ""),
        "content": text,
        "intro": str(article.get("summary") or ""),
        "out-links": [
            [ref["url"], ref.get("text") or ""] for ref in article.get("observed_refs", [])
        ],
    }
    for name in _EXTRA_FIELDS:
        record[name] = article.get(name)
    record["images"] = [
        asset["url"] for asset in article.get("assets", []) if asset.get("role") == "inline_image"
    ]
    record["content_id"] = content_id_for(text)
    return record


def html_record(url: str, analysis: ArticleAnalysis) -> dict[str, Any]:
    return record_from_article(url, analysis.article)


def document_format(url: str, content_type: str) -> str:
    """The document's format: its URL suffix, else what its MIME type names, else ``bin``."""
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    if suffix and suffix.isalnum() and len(suffix) <= _MAX_SUFFIX:
        return suffix
    return _MIME_FORMATS.get(content_type.partition(";")[0].strip().lower(), "bin")


def other_record(url: str, fmt: str, text: str) -> dict[str, Any]:
    return {
        "url": url,
        "format": fmt,
        "title": "",
        "content": text,
        "out-links": [],
        "content_id": content_id_for(text),
    }
