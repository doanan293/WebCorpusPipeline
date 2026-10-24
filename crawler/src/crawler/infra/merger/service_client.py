from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import aiohttp

from crawler.application.merger.ports import SendResult, Target
from crawler.infra.shared.http_client import BACKOFF_SECONDS, post


class HttpServiceClient:
    """The ``ServiceClient`` port: ``POST /add`` of the storage or the mediator block."""

    def __init__(
        self, storage_url: str, mediator_url: str, backoff: Sequence[float] = BACKOFF_SECONDS
    ) -> None:
        self.backoff = backoff
        self.urls: dict[Target, str] = {
            "storage": f"{storage_url.rstrip('/')}/add",
            "mediator": f"{mediator_url.rstrip('/')}/add",
        }
        self.session: aiohttp.ClientSession | None = None

    async def send(self, target: Target, payload: dict[str, Any]) -> SendResult:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return await post(self.session, self.urls[target], json=payload, backoff=self.backoff)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
