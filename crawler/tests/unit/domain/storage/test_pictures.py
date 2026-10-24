from __future__ import annotations

from crawler.domain.storage.pictures import MAX_PICTURE_BYTES, encode_picture, picture_format


def test_picture_format() -> None:
    assert picture_format("image/jpeg", "https://a/x.png") == "jpg"
    assert picture_format("image/svg+xml; charset=utf-8", "https://a/x") == "svg"
    assert picture_format("", "https://a/x.JPEG?w=1") == "jpg"
    assert picture_format("", "https://a/thumb.php?id=1") == ""
    assert picture_format("application/octet-stream", "https://a/x.webp") == "webp"


def test_encode_picture() -> None:
    assert encode_picture(b"\x89PNG") == "iVBORw=="
    assert MAX_PICTURE_BYTES == 2 * 1024 * 1024
