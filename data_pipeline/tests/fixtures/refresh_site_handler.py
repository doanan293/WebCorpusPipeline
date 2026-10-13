from __future__ import annotations

import hashlib
import time
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse, Request, Response

# Day granularity keeps page bytes (and ETags) identical across processes.
RECENT = time.strftime("%Y-%m-%dT00:00:00+00:00", time.gmtime(time.time() - 3 * 86400))
OLD = "2020-01-01T08:00:00+07:00"


def article(slug: str, version: int, published: str, extra: str = "") -> str:
    return (
        f'<html><head><meta property="article:published_time" content="{published}"></head>'
        f"<body><main><h1>Bài {slug}</h1><p>Nội dung bài {slug} phiên bản {version}. "
        "Đây là đoạn văn bản đủ dài để trích xuất bài viết một cách ổn định.</p>"
        f"{extra}</main></body></html>"
    )


def listing(title: str, slugs: list[str], pages: list[int]) -> str:
    items = "".join(f'<li><a href="/bai/{slug}">Bài {slug}</a></li>' for slug in slugs)
    pager = "".join(
        f'<a href="/tin-tuc?page={number}">{number}</a>'
        if number > 1
        else '<a href="/tin-tuc">1</a>'
        for number in pages
    )
    return (
        f"<html><body><main><h1>{title}</h1><p>Danh sách bài mới nhất của khoa: {', '.join(slugs)}.</p>"
        f"<ul>{items}</ul><nav>{pager}</nav></main></body></html>"
    )


HOME = (
    "<html><body><main><h1>Trang chủ</h1><p>Cổng thông tin tin tức của khoa.</p>"
    '<a href="/tin-tuc">Tin tức</a><a href="/gioi-thieu">Giới thiệu</a></main></body></html>'
)
ABOUT = (
    "<html><body><main><h1>Giới thiệu</h1>"
    "<p>Lịch sử hình thành và phát triển của khoa qua nhiều năm.</p></main></body></html>"
)
COMMON = {
    "/": HOME,
    "/gioi-thieu": ABOUT,
    **{f"/bai/a{n}": article(f"a{n}", 1, OLD) for n in range(1, 5)},
}
SITES = {
    1: {
        **COMMON,
        "/tin-tuc": listing("Tin tức", ["a5", "a4", "a3"], [2]),
        "/tin-tuc?page=2": listing("Tin tức trang 2", ["a2", "a1"], [1]),
        "/bai/a5": article("a5", 1, RECENT),
    },
    2: {
        **COMMON,
        "/tin-tuc": listing("Tin tức", ["n1", "a5", "a4"], [2]),
        "/tin-tuc?page=2": listing("Tin tức trang 2", ["n2", "a3", "a2"], [1, 3]),
        "/tin-tuc?page=3": listing("Tin tức trang 3", ["a1"], [2]),
        "/bai/a5": article("a5", 2, RECENT),
        "/bai/n1": article("n1", 1, RECENT, '<p><a href="/bai/n3">Bài liên quan n3</a></p>'),
        "/bai/n2": article("n2", 1, OLD),
        "/bai/n3": article("n3", 1, OLD),
    },
}


class RefreshSiteHandler:
    """Serve a versioned in-memory news site with ETag support."""

    lazy = True

    def __init__(self, settings, crawler=None):
        self.request_log = settings.get("FIXTURE_REQUEST_LOG")
        self.version = int(settings.get("FIXTURE_SITE_VERSION", 1))
        self.fail_paths = {p for p in str(settings.get("FIXTURE_FAIL_PATHS", "")).split(",") if p}

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings, crawler)

    async def close(self):
        return None

    async def download_request(self, request: Request):
        if self.request_log:
            with open(self.request_log, "a", encoding="utf-8") as log:
                log.write(f"{request.url}\n")
        parts = urlsplit(request.url)
        key = parts.path + (f"?{parts.query}" if parts.query else "")
        if key in self.fail_paths:
            return HtmlResponse(
                request.url,
                status=500,
                headers={b"Content-Type": b"text/html"},
                body=b"<h1>Server error</h1>",
                encoding="utf-8",
                request=request,
            )
        body = SITES[self.version].get(key) if parts.hostname == "news.test" else None
        if body is None:
            return Response(request.url, status=404, body=b"", request=request)
        encoded = body.encode("utf-8")
        etag = '"' + hashlib.sha256(encoded).hexdigest()[:16] + '"'
        if request.headers.get(b"If-None-Match", b"").decode("latin1") == etag:
            return Response(
                request.url, status=304, headers={b"ETag": etag.encode()}, body=b"", request=request
            )
        return HtmlResponse(
            request.url,
            status=200,
            headers={b"Content-Type": b"text/html; charset=utf-8", b"ETag": etag.encode()},
            body=encoded,
            encoding="utf-8",
            request=request,
        )
