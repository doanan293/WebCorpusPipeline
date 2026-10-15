import asyncio
from typing import Any
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse, Request, Response

PAGES_PER_HOST = 300
LINKS_PER_PAGE = 10
LATENCY_SECONDS = 0.02


def page_body(host: str, number: int) -> bytes:
    links = "".join(
        f'<a href="/bai/{n}">Bài số {n} của {host}</a>'
        # A tree: page n lists pages 10n+1..10n+10, like a category page listing articles.
        for n in range(number * LINKS_PER_PAGE + 1, (number + 1) * LINKS_PER_PAGE + 1)
        if n <= PAGES_PER_HOST
    )
    return (
        f"<html><body><main><h1>Bài {number} của {host}</h1>"
        f"<p>Nội dung riêng của bài {number} trên {host}, đủ dài để trích xuất ổn định.</p>"
        f"{links}</main></body></html>"
    ).encode()


class LatencyDownloadHandler:
    """Serve generated pages in-process after a fixed delay, like a server 20 ms away."""

    lazy = True

    def __init__(self, settings: Any, crawler: Any = None) -> None:
        self.settings = settings

    @classmethod
    def from_crawler(cls, crawler: Any) -> "LatencyDownloadHandler":
        return cls(crawler.settings, crawler)

    async def close(self) -> None:
        return None

    async def download_request(self, request: Request) -> Response:
        await asyncio.sleep(LATENCY_SECONDS)
        # Scrapy's HTTP handler records this; AutoThrottle adapts the slot delay from it.
        request.meta["download_latency"] = LATENCY_SECONDS
        parts = urlsplit(request.url)
        path = parts.path.rstrip("/")
        number = 0 if not path else int(path.rsplit("/", 1)[-1]) if path.startswith("/bai/") else -1
        if number < 0:
            return Response(request.url, status=404, body=b"Not found", request=request)
        return HtmlResponse(
            request.url,
            status=200,
            body=page_body(parts.hostname or "", number),
            encoding="utf-8",
            headers={b"Content-Type": b"text/html; charset=utf-8"},
            request=request,
        )
