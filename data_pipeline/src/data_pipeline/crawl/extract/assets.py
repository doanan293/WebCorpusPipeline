from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from lxml import etree

from data_pipeline.crawl.extract.links import normalize_url, normalized_text
from data_pipeline.crawl.frontier.assets import DOCUMENT_EXTENSIONS, DOCUMENT_MIME_TYPES

AssetRole = Literal["inline_image", "media_reference", "document_attachment"]


@dataclass(frozen=True, slots=True)
class ArticleAsset:
    url: str
    role: AssetRole
    alt: str = ""
    caption: str = ""
    external: bool = False


def _srcset_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip().split()[0] for part in value.split(",") if part.strip())


def _is_supported_document(value: str, declared_type: str = "") -> bool:

    mime = declared_type.partition(";")[0].strip().lower()
    if mime in DOCUMENT_MIME_TYPES:
        return True
    try:
        path = urlsplit(value).path
    except ValueError:
        return False
    return PurePosixPath(path).suffix.lower() in DOCUMENT_EXTENSIONS


def _append_asset(
    assets: list[ArticleAsset],
    seen: set[tuple[str, AssetRole]],
    *,
    value: str,
    base_url: str,
    role: AssetRole,
    alt: str = "",
    caption: str = "",
) -> None:
    normalized = normalize_url(value, base_url)
    if normalized is None or (normalized, role) in seen:
        return
    seen.add((normalized, role))
    assets.append(
        ArticleAsset(
            url=normalized,
            role=role,
            alt=alt,
            caption=caption,
            external=(urlsplit(normalized).hostname != urlsplit(base_url).hostname),
        )
    )


def extract_assets(
    element: etree._Element,
    base_url: str,
    warnings: list[str] | None = None,
) -> list[ArticleAsset]:
    assets: list[ArticleAsset] = []
    seen: set[tuple[str, AssetRole]] = set()

    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        tag = node.tag.lower()
        if tag == "img":
            caption = ""
            parent = node.getparent()
            while parent is not None and parent != element:
                if isinstance(parent.tag, str) and parent.tag.lower() == "figure":
                    cap_el = parent.find(".//figcaption")
                    if cap_el is not None:
                        caption = normalized_text(list(cap_el.itertext()))
                    break
                parent = parent.getparent()
            alt = node.get("alt", "")
            src = node.get("src")
            if src:
                _append_asset(
                    assets,
                    seen,
                    value=src,
                    base_url=base_url,
                    role="inline_image",
                    alt=alt,
                    caption=caption,
                )
            srcset = node.get("srcset")
            if srcset:
                for v in _srcset_values(srcset):
                    _append_asset(
                        assets,
                        seen,
                        value=v,
                        base_url=base_url,
                        role="inline_image",
                        alt=alt,
                        caption=caption,
                    )
        elif tag == "source":
            parent = node.getparent()
            parent_tag = (
                parent.tag.lower() if parent is not None and isinstance(parent.tag, str) else ""
            )
            if parent_tag == "picture":
                srcset = node.get("srcset")
                if srcset:
                    for v in _srcset_values(srcset):
                        _append_asset(assets, seen, value=v, base_url=base_url, role="inline_image")
                src = node.get("src")
                if src:
                    _append_asset(assets, seen, value=src, base_url=base_url, role="inline_image")
            elif parent_tag in {"video", "audio"}:
                src = node.get("src")
                if src:
                    _append_asset(
                        assets, seen, value=src, base_url=base_url, role="media_reference"
                    )
        elif tag == "video":
            src = node.get("src")
            if src:
                _append_asset(
                    assets,
                    seen,
                    value=src,
                    base_url=base_url,
                    role="media_reference",
                    caption=node.get("title", ""),
                )
            poster = node.get("poster")
            if poster:
                _append_asset(assets, seen, value=poster, base_url=base_url, role="inline_image")
        elif tag == "audio":
            src = node.get("src")
            if src:
                _append_asset(
                    assets,
                    seen,
                    value=src,
                    base_url=base_url,
                    role="media_reference",
                    caption=node.get("title", ""),
                )
        elif tag == "a":
            href = node.get("href")
            if href and _is_supported_document(href, node.get("type", "")):
                text = normalized_text(list(node.itertext()))
                _append_asset(
                    assets,
                    seen,
                    value=href,
                    base_url=base_url,
                    role="document_attachment",
                    caption=text,
                )
        elif tag == "object":
            data = node.get("data")
            if data and _is_supported_document(data, node.get("type", "")):
                text = normalized_text(list(node.itertext()))
                _append_asset(
                    assets,
                    seen,
                    value=data,
                    base_url=base_url,
                    role="document_attachment",
                    caption=text,
                )
        elif tag == "embed":
            src = node.get("src")
            if src and _is_supported_document(src, node.get("type", "")):
                _append_asset(
                    assets,
                    seen,
                    value=src,
                    base_url=base_url,
                    role="document_attachment",
                    caption=node.get("title", ""),
                )

    if warnings is not None:
        for asset in assets:
            if asset.external:
                warnings.append(f"external_asset:{asset.url}")

    return assets
