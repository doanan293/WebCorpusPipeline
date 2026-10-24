from __future__ import annotations

import aiohttp

from data_pipeline.domain.downloader.models import Fetched
from data_pipeline.infrastructure.shared.http_client import send

PARSE_TIMEOUT_SECONDS = 120.0


class HttpReaderClient:
    """The ``ReaderClient`` port: raw bytes to ``POST {reader_url}/parse`` with X-Url/X-Format."""

    def __init__(self, reader_url: str) -> None:
        self.url = f"{reader_url.rstrip('/')}/parse"
        self.session: aiohttp.ClientSession | None = None

    async def send(self, page: Fetched, fmt: str) -> bool:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return await send(
            self.session,
            self.url,
            data=page.body,
            headers={
                "X-Url": page.final_url,
                "X-Format": fmt,
                "Content-Type": page.content_type or "application/octet-stream",
            },
            timeout=PARSE_TIMEOUT_SECONDS,
        )

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
