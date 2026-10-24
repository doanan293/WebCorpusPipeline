from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from crawler.application.storage.pictures import PictureFetchSettings, fetch_pictures
from crawler.application.storage.ports import ImageFetchError, ImageResult
from crawler.domain.storage.pictures import MAX_PICTURE_BYTES

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class _Store:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    def pending(self, *, retry_errors: bool) -> list[str]:
        return [
            url
            for url, row in self.rows.items()
            if row["b64"] == "" and (row["error"] is None or retry_errors)
        ]

    def save_picture(
        self, url: str, *, b64: str, fmt: str, size: int | None, error: str | None, fetched_at: str
    ) -> None:
        self.rows[url].update(b64=b64, format=fmt, size=size, error=error, fetched_at=fetched_at)


class _Getter:
    def __init__(self, answers: dict[str, list[ImageResult | Exception]]) -> None:
        self.answers = answers
        self.calls: Counter[str] = Counter()

    async def get(self, url: str) -> ImageResult:
        self.calls[url] += 1
        answer = self.answers[url].pop(0) if len(self.answers[url]) > 1 else self.answers[url][0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def close(self) -> None:
        return None


async def _no_sleep(_: float) -> None:
    return None


def _row() -> dict[str, Any]:
    return {"b64": "", "error": None, "format": "", "size": None, "fetched_at": None}


def _run(store: _Store, getter: _Getter, retry_errors: bool = False) -> Counter[str]:
    return asyncio.run(
        fetch_pictures(
            store,
            getter,
            now_iso="t",
            settings=PictureFetchSettings(workers=4, retry_errors=retry_errors),
            sleep=_no_sleep,
        )
    )


def test_fetch_pictures_saves_base64_and_reasons() -> None:
    urls = [
        "https://a/ok.png",
        "https://a/page.jpg",
        "https://b/big.jpg",
        "https://b/gone.jpg",
        "https://c/slow.jpg",
    ]
    store = _Store(
        {url: _row() for url in urls} | {"https://a/done.png": {**_row(), "b64": "AAAA"}}
    )
    getter = _Getter(
        {
            "https://a/ok.png": [ImageResult(200, "image/png", PNG)],
            "https://a/page.jpg": [ImageResult(200, "text/html", b"<html>")],
            "https://b/big.jpg": [ImageResult(200, "image/jpeg", b"x" * (MAX_PICTURE_BYTES + 1))],
            "https://b/gone.jpg": [ImageResult(404, "text/html", b"")],
            "https://c/slow.jpg": [ImageFetchError("timeout")],
            "https://a/done.png": [ImageResult(200, "image/png", PNG)],
        }
    )
    counts = _run(store, getter)
    ok = store.rows["https://a/ok.png"]
    assert (ok["format"], ok["size"], ok["error"]) == ("png", len(PNG), None)
    assert ok["b64"].startswith("iVBORw0KGg")
    assert [store.rows[url]["error"] for url in urls[1:]] == [
        "not_image",
        "too_large",
        "http_404",
        "timeout",
    ]
    assert all(store.rows[url]["b64"] == "" for url in urls[1:])
    assert getter.calls["https://a/done.png"] == 0
    assert getter.calls["https://c/slow.jpg"] == 3  # retried in the same run
    assert counts["ok"] == 1


def test_busy_host_is_retried_then_saved() -> None:
    store = _Store({"https://a/x.png": _row()})
    getter = _Getter(
        {"https://a/x.png": [ImageResult(503, "", b""), ImageResult(200, "image/png", PNG)]}
    )
    _run(store, getter)
    assert store.rows["https://a/x.png"]["error"] is None
    assert store.rows["https://a/x.png"]["b64"]


def test_retry_errors_tries_failed_pictures_again() -> None:
    store = _Store({"https://a/x.png": {**_row(), "error": "timeout"}})
    getter = _Getter({"https://a/x.png": [ImageResult(200, "image/png", PNG)]})
    _run(store, getter)
    assert getter.calls["https://a/x.png"] == 0
    _run(store, getter, retry_errors=True)
    assert store.rows["https://a/x.png"]["b64"]
