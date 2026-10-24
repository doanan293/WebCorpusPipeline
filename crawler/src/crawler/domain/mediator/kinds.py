from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlsplit

from crawler.domain.mediator.models import UrlKind
from crawler.domain.shared.navigation import is_listing_page

DOCUMENT_SUFFIXES = frozenset(
    {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
        ".rtf", ".txt", ".csv", ".zip", ".rar", ".7z",
    }
)  # fmt: skip


def url_kind(url: str) -> UrlKind:
    """``document`` by file suffix, ``hub`` for a front page or a listing page, else ``page``."""
    path = urlsplit(url).path
    if PurePosixPath(path).suffix.lower() in DOCUMENT_SUFFIXES:
        return "document"
    return "hub" if path in {"", "/"} or is_listing_page(url) else "page"
