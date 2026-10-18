from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_pipeline.domain.parser.extract.assets import ArticleAsset, extract_assets
from data_pipeline.domain.parser.extract.blocks import blocks_from_article, text_from_blocks
from data_pipeline.domain.parser.extract.content import select_content
from data_pipeline.domain.parser.extract.links import (
    HtmlDiscovery,
    discoveries_from_root,
    document_base,
    extract_links,
)
from data_pipeline.domain.parser.extract.metadata import (
    extract_author,
    extract_canonical_url,
    extract_language,
    extract_modified_at,
    extract_published_at,
    extract_summary,
    extract_title,
    parse_article_html,
)
from data_pipeline.domain.parser.extract.sanitize import sanitize_article, serialize_content_html


@dataclass(frozen=True, slots=True)
class ArticleAnalysis:
    article: dict[str, Any]
    discoveries: list[HtmlDiscovery]
    assets: tuple[ArticleAsset, ...]


def analyze_article(source: bytes | str, url: str, encoding: str | None = None) -> ArticleAnalysis:
    root = parse_article_html(source, encoding)
    base_url = document_base(root, url)
    content_el = select_content(root)

    title = extract_title(root, content_el)
    warnings: list[str] = []

    if not title:
        warnings.append("missing_title")

    language = extract_language(root)
    canonical_url = extract_canonical_url(root, base_url, warnings)
    author = extract_author(root)
    published_at = extract_published_at(root, content_el, warnings)
    modified_at = extract_modified_at(root, warnings)
    summary = extract_summary(root)

    assets_list = extract_assets(content_el, base_url, warnings)
    assets: tuple[ArticleAsset, ...] = tuple(assets_list)

    sanitized_el = sanitize_article(content_el, base_url)
    content_html = serialize_content_html(sanitized_el)
    content_blocks = blocks_from_article(sanitized_el)
    text = text_from_blocks(content_blocks)

    if not text:
        warnings.append("missing_text")

    headings = [
        {
            "level": b["level"],
            "text": b["text"],
            "heading_path": list(b["heading_path"]),
        }
        for b in content_blocks
        if b["type"] == "heading"
    ]

    article: dict[str, Any] = {
        "title": title,
        "text": text,
        "language": language,
        "canonical_url": canonical_url,
        "author": author,
        "published_at": published_at,
        "modified_at": modified_at,
        "summary": summary,
        "content_html": content_html,
        "headings": headings,
        "content_blocks": content_blocks,
        "assets": [
            {
                "url": a.url,
                "role": a.role,
                "alt": a.alt,
                "caption": a.caption,
                "external": a.external,
            }
            for a in assets
        ],
        "links": extract_links(root, base_url),
        "warnings": warnings,
    }
    discoveries = discoveries_from_root(root, base_url)
    article["observed_refs"] = [dict(item) for item in discoveries]

    return ArticleAnalysis(article=article, discoveries=discoveries, assets=assets)
