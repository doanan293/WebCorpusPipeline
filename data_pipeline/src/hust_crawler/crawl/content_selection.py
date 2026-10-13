"""Explainable main-content selection used by the crawler."""

from dataclasses import dataclass

from lxml import etree

from hust_crawler.crawl.content import (
    _candidate_score,
    _select_content,
    _normalized_text,
    _parse_html,
)


@dataclass(frozen=True)
class ContentSelection:
    element: etree._Element
    score: int
    reason: str


def select_content(source: bytes | str, *, encoding: str | None = None) -> ContentSelection:
    root = _parse_html(source, encoding)
    element = _select_content(root)
    reason = (
        "semantic article/main"
        if element.tag in {"article", "main"}
        else "content-density fallback"
    )
    return ContentSelection(element, _candidate_score(element), reason)


def plain_text(source: bytes | str, *, encoding: str | None = None) -> str:
    selection = select_content(source, encoding=encoding)
    return " ".join(_normalized_text(list(selection.element.itertext())).split())
