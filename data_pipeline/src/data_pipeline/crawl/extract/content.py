from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from lxml import etree

from data_pipeline.crawl.extract.links import HtmlDiscovery, normalized_text

NONCONTENT_TAGS = {
    "aside",
    "button",
    "canvas",
    "dialog",
    "footer",
    "form",
    "header",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}


_BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
}


_POSITIVE_HINT = re.compile(
    r"(?:^|[-_\s])(article|content|detail|entry|main|news|post|story)(?:$|[-_\s])", re.I
)


NEGATIVE_HINT = re.compile(
    r"(?:^|[-_\s])(breadcrumb|comment|footer|header|menu|nav|related|share|sidebar|social)(?:$|[-_\s])",
    re.I,
)


def _candidate_score(element: etree._Element) -> int:
    text_length = len(normalized_text(list(element.itertext())))
    paragraph_bonus = 80 * len(element.xpath(".//p"))
    link_penalty = sum(
        len(normalized_text(list(link.itertext()))) for link in element.xpath(".//a")
    )
    return text_length + paragraph_bonus - link_penalty


def select_content(root: etree._Element) -> etree._Element:
    semantic = root.xpath("//article | //main")
    if semantic:
        return max(semantic, key=_candidate_score)

    hinted: list[etree._Element] = []
    for element in root.xpath("//*[@id or @class]"):
        hint = f"{element.get('id', '')} {element.get('class', '')}"
        if _POSITIVE_HINT.search(hint) and not NEGATIVE_HINT.search(hint):
            hinted.append(element)
    if hinted:
        return max(hinted, key=_candidate_score)

    body = root.xpath("//body")
    return body[0] if body else root


def _remove_noncontent(element: etree._Element) -> None:
    for child in list(element.iterdescendants()):
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        hints = f"{child.get('id', '')} {child.get('class', '')}"
        is_hidden = (
            child.get("hidden") is not None or (child.get("aria-hidden") or "").lower() == "true"
        )
        if not tag or tag in NONCONTENT_TAGS or NEGATIVE_HINT.search(hints) or is_hidden:
            child.drop_tree()


def _content_text(element: etree._Element) -> str:
    clean = deepcopy(element)
    _remove_noncontent(clean)
    pieces: list[str] = []

    def visit(node: etree._Element) -> None:
        if not isinstance(node.tag, str):
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag in _BLOCK_TAGS:
                pieces.append("\n")
            if child.tail:
                pieces.append(child.tail)

    visit(clean)
    lines = [" ".join(line.split()) for line in "".join(pieces).splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_title(root: etree._Element) -> str:
    for xpath in ("//title", "(//article | //main)//h1", "//h1"):
        for element in root.xpath(xpath):
            title = normalized_text(list(element.itertext()))
            if title:
                return title
    return ""


@dataclass(frozen=True, slots=True)
class HtmlAnalysis:
    article: dict[str, Any]
    discoveries: list[HtmlDiscovery]


def analyze_html(html: bytes | str, url: str, encoding: str | None = None) -> HtmlAnalysis:
    """Build DOM once and produce both article extraction and discoveries."""
    from data_pipeline.crawl.extract.article import analyze_article

    res = analyze_article(html, url, encoding)
    return HtmlAnalysis(article=res.article, discoveries=res.discoveries)


def extract_html(html: bytes | str, url: str, encoding: str | None = None) -> dict:
    """Extract the primary readable text and every valid HTTP anchor from HTML."""
    return analyze_html(html, url, encoding).article


def discover_html(html: bytes | str, url: str, encoding: str | None = None) -> list[dict]:
    """Discover linked pages and media without applying hostname scope."""
    return analyze_html(html, url, encoding).discoveries
