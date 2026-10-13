from __future__ import annotations

from copy import deepcopy

from lxml import etree
from lxml import html as lxml_html

from data_pipeline.crawl.extract.content import NEGATIVE_HINT, NONCONTENT_TAGS
from data_pipeline.crawl.extract.links import normalize_url

_ALLOWED_TAGS = {
    "article",
    "section",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "mark",
    "sub",
    "sup",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "figure",
    "figcaption",
    "picture",
    "source",
    "img",
    "hr",
}


_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height", "srcset"},
    "source": {"src", "srcset", "type", "media"},
    "ol": {"start", "type", "reversed"},
    "table": {"summary"},
    "th": {"colspan", "rowspan", "scope", "headers"},
    "td": {"colspan", "rowspan", "headers"},
    "blockquote": {"cite"},
}


_DROP_TAGS = {
    "aside",
    "audio",
    "button",
    "canvas",
    "dialog",
    "embed",
    "footer",
    "form",
    "header",
    "iframe",
    "input",
    "nav",
    "noscript",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
    "video",
}


def sanitize_article(element: etree._Element, base_url: str) -> etree._Element:
    clean = deepcopy(element)
    clean.attrib.clear()

    for comment in list(clean.xpath(".//comment()")):
        comment.drop_tree()

    for child in list(clean.iterdescendants()):
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        hints = f"{child.get('id', '')} {child.get('class', '')}"
        is_hidden = (
            child.get("hidden") is not None or (child.get("aria-hidden") or "").lower() == "true"
        )
        if tag in _DROP_TAGS or tag in NONCONTENT_TAGS or NEGATIVE_HINT.search(hints) or is_hidden:
            child.drop_tree()

    for child in list(clean.iterdescendants()):
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag not in _ALLOWED_TAGS:
            child.drop_tag()
            continue

        raw_attrs = dict(child.attrib)
        child.attrib.clear()
        allowed = _ALLOWED_ATTRS.get(tag, set())

        for key, val in raw_attrs.items():
            k = key.lower()
            if (
                k.startswith("on")
                or k in {"style", "class", "id"}
                or k.startswith("data-")
                or k not in allowed
            ):
                continue

            if tag == "a" and k == "href":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            elif tag in {"img", "source"} and k == "src":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            elif tag in {"img", "source"} and k == "srcset":
                candidates: list[str] = []
                for item in val.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    parts = item.split(maxsplit=1)
                    res = normalize_url(parts[0], base_url)
                    if res:
                        candidates.append(f"{res} {parts[1]}" if len(parts) > 1 else res)
                if candidates:
                    child.attrib[k] = ", ".join(candidates)
            elif tag == "blockquote" and k == "cite":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            else:
                child.attrib[k] = val

    return clean


def serialize_content_html(element: etree._Element) -> str:
    parts: list[str] = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    for child in element:
        parts.append(lxml_html.tostring(child, encoding="unicode").strip())
    return "\n".join(p for p in parts if p)
