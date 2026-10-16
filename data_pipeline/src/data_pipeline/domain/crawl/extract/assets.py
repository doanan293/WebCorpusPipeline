from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from lxml import etree

from data_pipeline.domain.crawl.extract.links import normalize_url, normalized_text
from data_pipeline.domain.crawl.frontier.assets import DOCUMENT_EXTENSIONS, DOCUMENT_MIME_TYPES

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


class _AssetCollector:
    """Collects the assets referenced by one article element, in document order."""

    def __init__(self, element: etree._Element, base_url: str) -> None:
        self.element = element
        self.base_url = base_url
        self.assets: list[ArticleAsset] = []
        self._seen: set[tuple[str, AssetRole]] = set()
        self._handlers = {
            "img": self._image,
            "source": self._source,
            "video": self._video,
            "audio": self._audio,
            "a": self._link,
            "object": self._object,
            "embed": self._embed,
        }

    def collect(self) -> list[ArticleAsset]:
        for node in self.element.iter():
            if not isinstance(node.tag, str):
                continue
            handler = self._handlers.get(node.tag.lower())
            if handler is not None:
                handler(node)
        return self.assets

    def _add(self, value: str, role: AssetRole, *, alt: str = "", caption: str = "") -> None:
        normalized = normalize_url(value, self.base_url)
        if normalized is None or (normalized, role) in self._seen:
            return
        self._seen.add((normalized, role))
        self.assets.append(
            ArticleAsset(
                url=normalized,
                role=role,
                alt=alt,
                caption=caption,
                external=(urlsplit(normalized).hostname != urlsplit(self.base_url).hostname),
            )
        )

    def _figure_caption(self, node: etree._Element) -> str:
        parent = node.getparent()
        while parent is not None and parent != self.element:
            if isinstance(parent.tag, str) and parent.tag.lower() == "figure":
                cap_el = parent.find(".//figcaption")
                return normalized_text(list(cap_el.itertext())) if cap_el is not None else ""
            parent = parent.getparent()
        return ""

    def _image(self, node: etree._Element) -> None:
        caption = self._figure_caption(node)
        alt = node.get("alt", "")
        src = node.get("src")
        if src:
            self._add(src, "inline_image", alt=alt, caption=caption)
        srcset = node.get("srcset")
        if srcset:
            for value in _srcset_values(srcset):
                self._add(value, "inline_image", alt=alt, caption=caption)

    def _source(self, node: etree._Element) -> None:
        parent = node.getparent()
        parent_tag = (
            parent.tag.lower() if parent is not None and isinstance(parent.tag, str) else ""
        )
        if parent_tag == "picture":
            srcset = node.get("srcset")
            if srcset:
                for value in _srcset_values(srcset):
                    self._add(value, "inline_image")
            src = node.get("src")
            if src:
                self._add(src, "inline_image")
        elif parent_tag in {"video", "audio"}:
            src = node.get("src")
            if src:
                self._add(src, "media_reference")

    def _video(self, node: etree._Element) -> None:
        src = node.get("src")
        if src:
            self._add(src, "media_reference", caption=node.get("title", ""))
        poster = node.get("poster")
        if poster:
            self._add(poster, "inline_image")

    def _audio(self, node: etree._Element) -> None:
        src = node.get("src")
        if src:
            self._add(src, "media_reference", caption=node.get("title", ""))

    def _link(self, node: etree._Element) -> None:
        href = node.get("href")
        if href and _is_supported_document(href, node.get("type", "")):
            self._add(href, "document_attachment", caption=normalized_text(list(node.itertext())))

    def _object(self, node: etree._Element) -> None:
        data = node.get("data")
        if data and _is_supported_document(data, node.get("type", "")):
            self._add(data, "document_attachment", caption=normalized_text(list(node.itertext())))

    def _embed(self, node: etree._Element) -> None:
        src = node.get("src")
        if src and _is_supported_document(src, node.get("type", "")):
            self._add(src, "document_attachment", caption=node.get("title", ""))


def extract_assets(
    element: etree._Element,
    base_url: str,
    warnings: list[str] | None = None,
) -> list[ArticleAsset]:
    assets = _AssetCollector(element, base_url).collect()
    if warnings is not None:
        warnings.extend(f"external_asset:{asset.url}" for asset in assets if asset.external)
    return assets
