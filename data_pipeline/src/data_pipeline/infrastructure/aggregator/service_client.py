from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import aiohttp

from data_pipeline.application.aggregator.ports import Target
from data_pipeline.infrastructure.shared.http_client import BACKOFF_SECONDS, send


class HttpServiceClient:
    """The ``ServiceClient`` port: ``POST /add`` of the storage or the controller block."""

    def __init__(
        self, storage_url: str, controller_url: str, backoff: Sequence[float] = BACKOFF_SECONDS
    ) -> None:
        self.backoff = backoff
        self.urls: dict[Target, str] = {
            "storage": f"{storage_url.rstrip('/')}/add",
            "controller": f"{controller_url.rstrip('/')}/add",
        }
        self.session: aiohttp.ClientSession | None = None

    async def send(self, target: Target, payload: dict[str, Any]) -> bool:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return await send(self.session, self.urls[target], json=payload, backoff=self.backoff)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
