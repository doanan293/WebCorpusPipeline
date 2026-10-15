from __future__ import annotations

from typing import Literal

_IMAGE_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
)

RefRelation = Literal["hyperlink", "image", "asset", "observed"]


def classify_ref(target_url: str, *, role: str | None, kind: str | None) -> RefRelation:
    """Name the relation of a page → target edge, the same way for the graph and for inventory.

    ``role`` comes from an article asset, ``kind`` from an observed reference; with neither the
    edge is an ordinary hyperlink.
    """
    if role is not None:
        return "image" if role == "inline_image" else "asset"
    if kind is not None:
        path = target_url.lower().split("?", 1)[0]
        return "image" if kind == "file" and path.endswith(_IMAGE_SUFFIXES) else "observed"
    return "hyperlink"
