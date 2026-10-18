from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import aiohttp

from data_pipeline.domain.fetcher.formats import payload_format
from data_pipeline.domain.fetcher.models import Fetched, FetcherConfig, FetchError
from data_pipeline.infrastructure.shared.network import (
    NonPublicAddressError,
    resolve_public_hostname,
    validate_public_web_url,
)

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CHUNK = 65536


async def public_address_check(url: str) -> None:
    """Refuse URLs that are not public web addresses, by literal IP or by what DNS answers."""
    decision = validate_public_web_url(url)
    if not decision.accepted or decision.hostname is None:
        raise FetchError(decision.reason or "malformed_url")
    port = urlsplit(url).port or (443 if url.startswith("https") else 80)
    try:
        await resolve_public_hostname(decision.hostname, port)
    except NonPublicAddressError as exc:
        raise FetchError("non_public_address") from exc
    except OSError as exc:
        raise FetchError("dns_failed") from exc


async def _allow_any(_: str) -> None:
    return None


class AiohttpPageGetter:
    """The ``PageGetter`` port: follows redirects itself so every hop passes ``check``."""

    def __init__(
        self, config: FetcherConfig, check: Callable[[str], Awaitable[None]] | None = None
    ) -> None:
        self.config = config
        self.check = check or (
            _allow_any if config.allow_private_addresses else public_address_check
        )
        self.session: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(headers={"User-Agent": self.config.user_agent})
        return self.session

    async def get(self, url: str) -> Fetched:
        current = url
        for _ in range(self.config.max_redirects + 1):
            await self.check(current)
            try:
                async with self._session().get(
                    current,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response:
                    location = response.headers.get("Location")
                    if response.status in _REDIRECTS and location:
                        current = urljoin(current, location)
                        continue
                    content_type = response.headers.get("Content-Type", "")
                    limit = (
                        self.config.max_file_bytes
                        if payload_format(current, content_type) == "other"
                        else self.config.max_response_bytes
                    )
                    if response.content_length is not None and response.content_length > limit:
                        raise FetchError("too_large")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(_CHUNK):
                        body += chunk
                        if len(body) > limit:
                            raise FetchError("too_large")
                    return Fetched(current, response.status, content_type, bytes(body))
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise FetchError(type(exc).__name__) from exc
        raise FetchError("too_many_redirects")

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
