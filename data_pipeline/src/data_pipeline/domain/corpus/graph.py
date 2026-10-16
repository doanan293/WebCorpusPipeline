from __future__ import annotations

from typing import Any
from urllib.parse import urldefrag

from data_pipeline.domain.shared.refs import classify_ref

type Edge = tuple[str, str, str, str, str, str]
"""(source_url, target_url, relation, anchor_text, alt, caption)."""


def clean_url(value: object) -> str:
    return urldefrag(str(value or "").strip())[0]


def page_edges(row: dict[str, Any]) -> tuple[list[Edge], list[str]]:
    """The edges out of one crawled page, and the image URLs among their targets."""
    edges: list[Edge] = []
    images: list[str] = []
    page = clean_url(row.get("url") or row.get("final_url"))
    if not page:
        return edges, images

    def add(item: dict[str, Any], *, role: str | None, kind: str | None) -> None:
        target = clean_url(item.get("url"))
        relation = classify_ref(target, role=role, kind=kind)
        edges.append(
            (
                page,
                target,
                relation,
                str(item.get("text") or "") if kind is not None else "",
                str(item.get("alt") or ""),
                str(item.get("caption") or "") if role is not None else "",
            )
        )
        if relation == "image":
            images.append(target)

    for item in row.get("links") or []:
        target = clean_url(item.get("url")) if isinstance(item, dict) else ""
        if target:
            edges.append((page, target, "hyperlink", str(item.get("text") or ""), "", ""))
    for item in row.get("assets") or []:
        if isinstance(item, dict) and clean_url(item.get("url")):
            add(item, role=str(item.get("role") or "asset"), kind=None)
    for item in row.get("observed_refs") or []:
        if isinstance(item, dict) and clean_url(item.get("url")):
            add(item, role=None, kind=str(item.get("kind") or ""))
    return edges, images
