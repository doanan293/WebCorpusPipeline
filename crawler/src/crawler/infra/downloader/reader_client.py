from __future__ import annotations

import aiohttp

from crawler.domain.downloader.models import Fetched
from crawler.infra.shared.http_client import send

PARSE_TIMEOUT_SECONDS = 120.0


class HttpReaderClient:
    """The ``ReaderClient`` port: raw bytes to ``POST {reader_url}/parse`` with X-Url/X-Format."""

    def __init__(self, reader_url: str) -> None:
        self.url = f"{reader_url.rstrip('/')}/parse"
        self.session: aiohttp.ClientSession | None = None

    async def send(self, page: Fetched, fmt: str, *, requested_url: str) -> bool:
        headers = {
            "X-Url": page.final_url,
            "X-Format": fmt,
            "X-Requested-Url": requested_url,
            "Content-Type": page.content_type or "application/octet-stream",
        }
        if page.etag:
            headers["X-Etag"] = page.etag
        if page.last_modified:
            headers["X-Last-Modified"] = page.last_modified
        return await self._post(page.body, headers)

    async def send_not_modified(self, requested_url: str) -> bool:
        headers = {"X-Url": requested_url, "X-Format": "not_modified"}
        return await self._post(b"", headers)

    async def _post(self, body: bytes, headers: dict[str, str]) -> bool:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return await send(
            self.session, self.url, data=body, headers=headers, timeout=PARSE_TIMEOUT_SECONDS
        )

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
