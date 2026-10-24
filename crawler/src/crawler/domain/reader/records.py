"""The records the reader block sends on: the slide's ``/add`` fields plus what extraction knows."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from crawler.domain.reader.extract.article import ArticleAnalysis
from crawler.domain.reader.extract.fingerprint import change_fingerprint

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


def fingerprint_for(text: str) -> str | None:
    """The change fingerprint the merger compares between visits; ``None`` for empty text."""
    return change_fingerprint(text) if text.strip() else None


def record_from_article(url: str, article: Mapping[str, Any]) -> dict[str, Any]:
    """An HTML record from an extracted article dict (fresh, or stored by the batch crawl)."""
    text = str(article.get("text") or "")
    refs = list(article.get("observed_refs", []))
    if any("sentence" in ref for ref in refs):
        # Out-links are links only; embedded resources (images, the NukeViet cron beacon
        # ``index.php?second=cronjobs&p=<random>``) are not. Older stored articles cannot tell.
        refs = [ref for ref in refs if "sentence" in ref]
    record: dict[str, Any] = {
        "url": url,
        "format": "html",
        "title": str(article.get("title") or ""),
        "content": text,
        "out-links": [
            # The slide's link text is the sentence holding the link; older stored articles have
            # only the link's own label.
            [ref["url"], ref.get("sentence") or ref.get("text") or ""]
            for ref in refs
        ],
    }
    for name in _EXTRA_FIELDS:
        record[name] = article.get(name)
    record["images"] = [
        asset["url"] for asset in article.get("assets", []) if asset.get("role") == "inline_image"
    ]
    record["content_id"] = content_id_for(text)
    record["fingerprint"] = fingerprint_for(text)
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
        "fingerprint": fingerprint_for(text),
    }
