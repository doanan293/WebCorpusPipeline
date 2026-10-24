from __future__ import annotations

import re

from lxml import etree

from data_pipeline.domain.reader.extract.links import normalized_text

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


_MIN_CONTENT_CHARS = 40

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
    body = root.xpath("//body")
    fallback = body[0] if body else root
    semantic = root.xpath("//article | //main")
    if semantic:
        best = max(semantic, key=_candidate_score)
        # An empty <main>/<article> is a script-filled shell, not the article.
        return best if normalized_text(list(best.itertext())) else fallback

    hinted: list[etree._Element] = []
    for element in root.xpath("//*[@id or @class]"):
        hint = f"{element.get('id', '')} {element.get('class', '')}"
        if _POSITIVE_HINT.search(hint) and not NEGATIVE_HINT.search(hint):
            hinted.append(element)
    if not hinted:
        return fallback
    best = max(hinted, key=_candidate_score)
    # A tiny class-hinted winner (an icon list) means the hints missed the article.
    if len(normalized_text(list(best.itertext()))) < _MIN_CONTENT_CHARS:
        return fallback
    return best
