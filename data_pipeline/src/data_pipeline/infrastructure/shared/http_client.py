from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BACKOFF_SECONDS = (1.0, 2.0, 4.0)


async def send(
    session: aiohttp.ClientSession,
    url: str,
    *,
    json: Mapping[str, Any] | None = None,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    backoff: Sequence[float] = BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """POST to another block; retry on connection errors, timeouts and 5xx, never on 4xx."""
    delays = list(backoff)
    while True:
        try:
            async with session.post(
                url,
                json=json,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status < HTTPStatus.INTERNAL_SERVER_ERROR:
                    return HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES
                reason = f"http_{response.status}"
        except (aiohttp.ClientError, TimeoutError) as exc:
            reason = type(exc).__name__
        if not delays:
            log.warning("giving up on %s: %s", url, reason)
            return False
        await sleep(delays.pop(0))
