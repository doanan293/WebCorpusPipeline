from __future__ import annotations

from crawler.domain.storage.pictures import (
    MAX_PICTURE_BYTES,
    encode_picture,
    picture_format,
    sniff_picture,
)


def test_picture_format() -> None:
    assert picture_format("image/jpeg", "https://a/x.png") == "jpg"
    assert picture_format("image/svg+xml; charset=utf-8", "https://a/x") == "svg"
    assert picture_format("", "https://a/x.JPEG?w=1") == "jpg"
    assert picture_format("", "https://a/thumb.php?id=1") == ""
    assert picture_format("application/octet-stream", "https://a/x.webp") == "webp"


def test_encode_picture() -> None:
    assert encode_picture(b"\x89PNG") == "iVBORw=="
    assert MAX_PICTURE_BYTES == 2 * 1024 * 1024


def test_sniff_picture() -> None:
    assert sniff_picture(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert sniff_picture(b"\x89PNG\r\n\x1a\nrest") == "png"
    assert sniff_picture(b"GIF89a") == "gif"
    assert sniff_picture(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_picture(b"<!doctype html>") == ""
