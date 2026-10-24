from __future__ import annotations

from typing import Literal

from crawler.domain.shared.assets import recognizable_asset

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


def payload_format(url: str, content_type: str) -> Literal["html", "other"] | None:
    """What the reader block receives: ``html``, a document as ``other``, or nothing."""
    if content_type.partition(";")[0].strip().lower() in _HTML_TYPES:
        return "html"
    if recognizable_asset(url, content_type) == "document":
        return "other"
    return None
