from __future__ import annotations

from copy import deepcopy

from lxml import etree
from lxml import html as lxml_html

from data_pipeline.domain.parser.extract.content import NEGATIVE_HINT, NONCONTENT_TAGS
from data_pipeline.domain.shared.links import normalize_url

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


def _is_removable(child: etree._Element) -> bool:
    tag = str(child.tag).lower()
    hints = f"{child.get('id', '')} {child.get('class', '')}"
    is_hidden = (
        child.get("hidden") is not None or (child.get("aria-hidden") or "").lower() == "true"
    )
    return (
        tag in _DROP_TAGS
        or tag in NONCONTENT_TAGS
        or bool(NEGATIVE_HINT.search(hints))
        or is_hidden
    )


def _normalized_srcset(value: str, base_url: str) -> str:
    candidates: list[str] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        parts = item.split(maxsplit=1)
        resolved = normalize_url(parts[0], base_url)
        if resolved:
            candidates.append(f"{resolved} {parts[1]}" if len(parts) > 1 else resolved)
    return ", ".join(candidates)


def _is_allowed_attribute(key: str, allowed: set[str]) -> bool:
    return not (
        key.startswith("on")
        or key in {"style", "class", "id"}
        or key.startswith("data-")
        or key not in allowed
    )


def _attribute_value(tag: str, key: str, value: str, base_url: str) -> str | None:
    """Return the value to keep for an allowed attribute, or None to drop it."""
    if (
        (tag == "a" and key == "href")
        or (tag in {"img", "source"} and key == "src")
        or (tag == "blockquote" and key == "cite")
    ):
        return normalize_url(value, base_url) or None
    if tag in {"img", "source"} and key == "srcset":
        return _normalized_srcset(value, base_url) or None
    return value


def _clean_attributes(child: etree._Element, tag: str, base_url: str) -> None:
    raw_attrs = dict(child.attrib)
    child.attrib.clear()
    allowed = _ALLOWED_ATTRS.get(tag, set())
    for key, value in raw_attrs.items():
        name = key.lower()
        if not _is_allowed_attribute(name, allowed):
            continue
        kept = _attribute_value(tag, name, value, base_url)
        if kept is not None:
            child.attrib[name] = kept


def sanitize_article(element: etree._Element, base_url: str) -> etree._Element:
    clean = deepcopy(element)
    clean.attrib.clear()

    for comment in list(clean.xpath(".//comment()")):
        comment.drop_tree()

    for child in list(clean.iterdescendants()):
        if isinstance(child.tag, str) and _is_removable(child):
            child.drop_tree()

    for child in list(clean.iterdescendants()):
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag not in _ALLOWED_TAGS:
            child.drop_tag()
            continue
        _clean_attributes(child, tag, base_url)

    return clean


def serialize_content_html(element: etree._Element) -> str:
    parts: list[str] = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    parts.extend(lxml_html.tostring(child, encoding="unicode").strip() for child in element)
    return "\n".join(p for p in parts if p)
