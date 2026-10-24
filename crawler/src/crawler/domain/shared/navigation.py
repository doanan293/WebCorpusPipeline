"""Listing pages: where new articles show up first, so the mediator recrawls them as hubs."""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import parse_qsl, urlsplit

# Last path segment of a page that lists articles (news, notices, events, categories).
LISTING_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "tin-tuc", "tintuc", "tin-tuc-su-kien", "news", "thong-bao", "thongbao",
        "announcement", "announcements", "notice", "notices", "su-kien", "sukien", "event",
        "events", "chuyen-muc", "danh-muc", "category", "categories", "blog", "bai-viet",
    }
)  # fmt: skip
# A language root such as /vi/ or /en/ is a front page too.
LANGUAGE_ROOTS: Final[frozenset[str]] = frozenset({"vi", "en"})
PAGE_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"page", "p", "paged", "pg", "trang", "pagenumber"}
)
_PATH_PAGE_RE: Final[re.Pattern[str]] = re.compile(
    r"/(?:page|trang|p)/(\d+)/?$|/page-(\d+)/?$", re.IGNORECASE
)


def is_later_page(url: str) -> bool:
    """Page two or later of a list, by query (``?page=2``) or path (``/page/3``)."""
    parts = urlsplit(url)
    for key, value in parse_qsl(parts.query):
        if key.casefold() in PAGE_QUERY_KEYS and value.isdigit() and int(value) > 1:
            return True
    match = _PATH_PAGE_RE.search(parts.path)
    return match is not None and int(match.group(1) or match.group(2)) > 1


def is_listing_page(url: str) -> bool:
    """The first page of a list of articles, or a language root."""
    if is_later_page(url):
        return False
    segments = [segment.casefold() for segment in urlsplit(url).path.split("/") if segment]
    if not segments:
        return False
    if segments == [segments[0]] and segments[0] in LANGUAGE_ROOTS:
        return True
    return segments[-1] in LISTING_SEGMENTS
