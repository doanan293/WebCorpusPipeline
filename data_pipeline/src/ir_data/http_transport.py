from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass
class Response:
    status: int
    headers: Any
    final_url: str
    body: AsyncIterator[bytes]
    _response: aiohttp.ClientResponse

    async def close(self) -> None:
        self._response.release()


class PublicTransport:
    def __init__(self, settings):
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=None, sock_read=self.settings.timeout_seconds, connect=self.settings.timeout_seconds)
        self.session = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "ir-data/0.1", "Accept-Encoding": "identity"}, auto_decompress=False)
        return self

    async def __aexit__(self, *exc):
        if self.session: await self.session.close()

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        assert self.session is not None
        response = await self.session.get(url, headers=headers, allow_redirects=True, max_redirects=self.settings.max_redirects)
        return Response(response.status, response.headers, str(response.url), response.content.iter_chunked(self.settings.chunk_bytes), response)
