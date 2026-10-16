from __future__ import annotations

import re

from lxml import etree
from lxml import html as lxml_html

from data_pipeline.domain.crawl.extract.dates import parse_page_date
from data_pipeline.domain.crawl.extract.links import normalize_url, normalized_text

_XML_INCOMPATIBLE_CHARACTERS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def parse_article_html(source: bytes | str, encoding: str | None = None) -> etree._Element:
    if isinstance(source, str):
        source = source.encode("utf-8")
        encoding = "utf-8"
    if not source.strip():
        source = b"<html></html>"
    parser = lxml_html.HTMLParser(encoding=encoding, recover=True)
    root = lxml_html.document_fromstring(source, parser=parser)
    for element in root.iter():
        if element.text:
            element.text = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", element.text)
        if element.tail:
            element.tail = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", element.tail)
        for key, value in element.attrib.items():
            element.attrib[key] = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", value)
    return root


def extract_title(root: etree._Element, content_el: etree._Element) -> str:
    # 1. Visible h1 inside selected content container
    for h1 in content_el.xpath(".//h1"):
        text = normalized_text(list(h1.itertext()))
        if text:
            return text

    # 2. og:title
    for og_title in root.xpath(
        "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='og:title']/@content"
        " | //meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='og:title']/@content"
    ):
        text = " ".join(str(og_title).split())
        if text:
            return text

    # 3. Document <title>
    for title_el in root.xpath("//title"):
        text = normalized_text(list(title_el.itertext()))
        if text:
            return text

    # 4. Any h1 anywhere in document
    for h1 in root.xpath("//h1"):
        text = normalized_text(list(h1.itertext()))
        if text:
            return text

    return ""


def extract_meta_content(
    root: etree._Element, attribute_name: str, values: tuple[str, ...]
) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for val in values:
        xpath = (
            f"//meta[translate(@{attribute_name}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            f"'abcdefghijklmnopqrstuvwxyz')='{val.lower()}']/@content"
        )
        for raw in root.xpath(xpath):
            cleaned = " ".join(str(raw).split())
            if cleaned and cleaned not in seen:
                results.append(cleaned)
                seen.add(cleaned)
    return results


def extract_language(root: etree._Element) -> str | None:
    for lang in root.xpath("//html/@lang | //html/@xml:lang"):
        cleaned = " ".join(str(lang).split())
        if cleaned:
            return cleaned
    meta_langs = extract_meta_content(root, "http-equiv", ("content-language",))
    if meta_langs:
        return meta_langs[0]
    meta_langs = extract_meta_content(root, "name", ("content-language", "language"))
    if meta_langs:
        return meta_langs[0]
    return None


def extract_canonical_url(root: etree._Element, base_url: str, warnings: list[str]) -> str | None:
    raw_canonicals: list[str] = []
    for href in root.xpath(
        "//link[translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='canonical']/@href"
    ):
        resolved = normalize_url(str(href), base_url)
        if resolved and resolved not in raw_canonicals:
            raw_canonicals.append(resolved)
    if len(raw_canonicals) > 1:
        warnings.append("ambiguous_canonical_url")
    return raw_canonicals[0] if raw_canonicals else None


def extract_author(root: etree._Element) -> str | None:
    authors = extract_meta_content(root, "name", ("author", "article:author"))
    if not authors:
        authors = extract_meta_content(root, "property", ("author", "article:author"))
    return authors[0] if authors else None


def extract_published_at(
    root: etree._Element, content_el: etree._Element, warnings: list[str]
) -> str | None:
    dates: list[str] = []
    seen: set[str] = set()

    def add_date(raw: str) -> None:
        cleaned = " ".join(raw.split())
        if cleaned and cleaned not in seen:
            dates.append(cleaned)
            seen.add(cleaned)

    for val in extract_meta_content(
        root, "property", ("article:published_time", "og:published_time")
    ):
        add_date(val)
    for val in extract_meta_content(
        root, "name", ("article:published_time", "pubdate", "publishdate")
    ):
        add_date(val)

    for time_el in content_el.xpath(".//time[@datetime]"):
        val = time_el.get("datetime")
        if val:
            add_date(val)

    if not dates:
        for time_el in root.xpath("//time[@datetime]"):
            val = time_el.get("datetime")
            if val:
                add_date(val)

    if len(dates) > 1:
        warnings.append("ambiguous_published_at")
    return _iso_date(dates[0] if dates else None, "published_at", warnings)


def extract_modified_at(root: etree._Element, warnings: list[str]) -> str | None:
    dates = extract_meta_content(root, "property", ("article:modified_time", "og:updated_time"))
    if not dates:
        dates = extract_meta_content(root, "name", ("article:modified_time", "last-modified"))
    return _iso_date(dates[0] if dates else None, "modified_at", warnings)


def _iso_date(raw: str | None, field: str, warnings: list[str]) -> str | None:
    parsed = parse_page_date(raw)
    if raw and parsed is None:
        warnings.append(f"unparsed_date:{field}")
    return parsed


def extract_summary(root: etree._Element) -> str | None:
    summaries = extract_meta_content(
        root, "name", ("description", "og:description", "twitter:description")
    )
    if not summaries:
        summaries = extract_meta_content(root, "property", ("description", "og:description"))
    return summaries[0] if summaries else None
