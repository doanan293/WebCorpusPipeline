from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from lxml import etree

from data_pipeline.domain.shared.links import normalize_url

_HTML_EXTENSIONS = {".asp", ".aspx", ".cfm", ".htm", ".html", ".jsp", ".php"}


_FILE_EXTENSIONS = {
    ".7z",
    ".aac",
    ".avi",
    ".avif",
    ".bmp",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".flac",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ods",
    ".odt",
    ".ogg",
    ".ogv",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".svg",
    ".tar",
    ".tif",
    ".tiff",
    ".tsv",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


_TRANSIENT_EXTENSIONS = {
    ".css",
    ".eot",
    ".js",
    ".map",
    ".mjs",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
}


def normalized_text(values: list[str] | tuple[str, ...]) -> str:
    return " ".join(" ".join(values).split())


def document_base(root: etree._Element, url: str) -> str:
    final_url = normalize_url(url) or url
    base_values = root.xpath("//base[@href][1]/@href")
    if base_values:
        return normalize_url(str(base_values[0]), final_url) or final_url
    return final_url


def _element_label(element: etree._Element) -> str:
    visible = normalized_text(list(element.itertext()))
    if visible:
        return visible
    alt_values = [str(value) for value in element.xpath(".//*[@alt]/@alt")]
    own_alt = element.get("alt")
    if own_alt:
        alt_values.insert(0, own_alt)
    alt = normalized_text(alt_values)
    if alt:
        return alt
    return normalized_text([element.get("aria-label", "")]) or normalized_text(
        [element.get("title", "")]
    )


def extract_links(root: etree._Element, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in root.xpath("//a[@href]"):
        normalized = normalize_url(anchor.get("href", ""), base_url)
        if normalized is None:
            continue
        text = _element_label(anchor)
        identity = (normalized, text)
        if identity not in seen:
            links.append({"url": normalized, "text": text})
            seen.add(identity)
    return links


class HtmlDiscovery(TypedDict):
    url: str
    text: str
    kind: str
    rel: list[str]
    sentence: NotRequired[str]  # links only: the sentence holding the link (the slide's link text)


# Blocks of running text: a link inside one is quoted with the sentence around it.
_TEXT_BLOCKS = frozenset(
    {"p", "li", "td", "th", "dd", "dt", "blockquote", "figcaption", "caption"}
    | {f"h{level}" for level in range(1, 7)}
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_MAX_SENTENCE = 400


def anchor_sentence(element: etree._Element, label: str) -> str:
    """The whole sentence holding a link ("câu hoàn thiện chứa thẻ a" on the slide).

    Only within a block of running text; a link alone in its block (a menu item, a page number)
    or in a block too long to be a sentence keeps its own label.
    """
    if not label:
        return label
    block = next(
        (
            ancestor
            for ancestor in element.iterancestors()
            if isinstance(ancestor.tag, str) and ancestor.tag.lower() in _TEXT_BLOCKS
        ),
        None,
    )
    if block is None:
        return label
    for sentence in _SENTENCE_END.split(normalized_text(list(block.itertext()))):
        if label in sentence:
            return sentence if len(sentence) <= _MAX_SENTENCE else label
    return label


_TRANSIENT_MIMES = frozenset(
    {
        "text/css",
        "text/javascript",
        "application/font-woff",
        "application/javascript",
        "application/vnd.ms-fontobject",
        "application/x-javascript",
        "application/x-font-opentype",
        "application/x-font-ttf",
        "application/ecmascript",
        "text/ecmascript",
    }
)


def _kind_from_mime(mime: str) -> str:
    if mime in {"text/html", "application/xhtml+xml"}:
        return "html"
    if mime in _TRANSIENT_MIMES or mime.startswith("font/"):
        return "transient"
    if mime.startswith(("image/", "video/", "audio/", "application/", "text/")):
        return "file"
    return "unknown"


def _kind_from_extension(url: str) -> str:
    try:
        path = urlsplit(url).path
    except ValueError:
        return "unknown"
    suffix = PurePosixPath(path).suffix.lower()
    if not suffix or suffix in _HTML_EXTENSIONS:
        return "html"
    if suffix in _FILE_EXTENSIONS:
        return "file"
    if suffix in _TRANSIENT_EXTENSIONS:
        return "transient"
    return "unknown"


def resource_kind(url: str, content_type: str = "") -> str:
    """Classify a URL using response MIME when known, otherwise its extension."""
    mime = content_type.partition(";")[0].strip().lower()
    return _kind_from_mime(mime) if mime else _kind_from_extension(url)


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for raw_candidate in value.split(","):
        candidate = raw_candidate.strip()
        if candidate:
            urls.append(candidate.split()[0])
    return urls


_URL_ATTRIBUTES = ("href", "src", "data", "poster", "srcset")
_SRC_TAGS = frozenset({"audio", "embed", "iframe", "img", "input", "source", "track", "video"})


def _is_navigation_link(relation: set[str], element: etree._Element) -> bool:
    """Keep <link> only for pagination and language alternates, not icons or preconnects."""
    return bool(relation & {"next", "prev"}) or (
        "alternate" in relation and bool(element.get("hreflang"))
    )


def discoveries_from_root(root: etree._Element, base_url: str) -> list[HtmlDiscovery]:
    discoveries: list[HtmlDiscovery] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()

    def add(
        value: str,
        text: str,
        rel: tuple[str, ...] = (),
        *,
        kind_hint: str | None = None,
        sentence: str | None = None,
    ) -> None:
        normalized = normalize_url(value, base_url)
        if normalized is None:
            return
        kind = kind_hint or resource_kind(normalized)
        if kind == "transient":
            return
        sorted_rel = tuple(sorted(rel))
        identity = (normalized, text, kind, sorted_rel)
        if identity not in seen:
            discovery: HtmlDiscovery = {
                "url": normalized,
                "text": text,
                "kind": kind,
                "rel": list(sorted_rel),
            }
            if sentence is not None:
                discovery["sentence"] = sentence
            discoveries.append(discovery)
            seen.add(identity)

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()
        if not any(element.get(attribute) for attribute in _URL_ATTRIBUTES):
            continue
        relation = {token.lower() for token in element.get("rel", "").split()}
        as_kind = element.get("as", "").lower()
        if tag == "script" or "stylesheet" in relation or as_kind in {"font", "script", "style"}:
            continue
        if tag == "link" and not _is_navigation_link(relation, element):
            continue
        text = _element_label(element)
        if tag in {"a", "area"} and element.get("href"):
            add(
                element.get("href", ""),
                text,
                tuple(sorted(relation)),
                sentence=anchor_sentence(element, text),
            )
        if tag == "link" and element.get("href"):
            add(element.get("href", ""), text, tuple(sorted(relation)))
        if tag in _SRC_TAGS and element.get("src"):
            add(element.get("src", ""), text, (), kind_hint="file")
        if tag == "object" and element.get("data"):
            add(element.get("data", ""), text, (), kind_hint="file")
        if tag == "video" and element.get("poster"):
            add(element.get("poster", ""), text, (), kind_hint="file")
        if tag in {"img", "source"} and element.get("srcset"):
            for candidate in _srcset_urls(element.get("srcset", "")):
                add(candidate, text, (), kind_hint="file")
    return discoveries
