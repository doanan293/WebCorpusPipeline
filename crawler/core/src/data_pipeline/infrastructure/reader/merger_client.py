from __future__ import annotations

from typing import Any

import aiohttp

from data_pipeline.infrastructure.shared.http_client import send


class HttpMergerClient:
    """The ``MergerClient`` port: ``POST {merger_url}/add`` with the record as JSON."""

    def __init__(self, merger_url: str) -> None:
        self.url = f"{merger_url.rstrip('/')}/add"
        self.session: aiohttp.ClientSession | None = None

    async def send(self, record: dict[str, Any]) -> bool:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return await send(self.session, self.url, json=record, timeout=60.0)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
