"""The ``picture`` collection: an image's format and its bytes as base64 text."""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from urllib.parse import urlsplit

MAX_PICTURE_BYTES = 2 * 1024 * 1024
_MIME_FORMATS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/avif": "avif",
}
_SUFFIXES = {"jpeg": "jpg", "jpe": "jpg", "jfif": "jpg", "tif": "tiff"}
_KNOWN = frozenset(_MIME_FORMATS.values())


def picture_format(content_type: str, url: str) -> str:
    """``jpg``, ``png``, ...: from the MIME type, else the URL suffix, else ``""``."""
    mime = content_type.partition(";")[0].strip().lower()
    if mime in _MIME_FORMATS:
        return _MIME_FORMATS[mime]
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip(".")
    suffix = _SUFFIXES.get(suffix, suffix)
    return suffix if suffix in _KNOWN else ""


def encode_picture(body: bytes) -> str:
    """The image as plain base64, without a ``data:`` prefix."""
    return base64.b64encode(body).decode("ascii")
