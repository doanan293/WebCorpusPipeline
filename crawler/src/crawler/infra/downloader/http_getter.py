from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from urllib.parse import urljoin, urlsplit

import aiohttp

from crawler.domain.downloader.formats import payload_format
from crawler.domain.downloader.models import DownloaderConfig, Fetched, FetchError, Validators
from crawler.infra.shared.network import (
    NonPublicAddressError,
    resolve_public_hostname,
    validate_public_web_url,
)

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CHUNK = 65536


def _conditional_headers(validators: Validators | None) -> dict[str, str]:
    if validators is None:
        return {}
    headers: dict[str, str] = {}
    if validators.etag:
        headers["If-None-Match"] = validators.etag
    if validators.last_modified:
        headers["If-Modified-Since"] = validators.last_modified
    return headers


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


class _ConnectionFailedError(FetchError):
    """The host could not be reached or did not answer in time; worth another try."""


class AiohttpPageGetter:
    """The ``PageGetter`` port: follows redirects itself so every hop passes ``check``."""

    def __init__(
        self,
        config: DownloaderConfig,
        check: Callable[[str], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.sleep = sleep
        self.check = check or (
            _allow_any if config.allow_private_addresses else public_address_check
        )
        self.session: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(headers={"User-Agent": self.config.user_agent})
        return self.session

    async def get(self, url: str, validators: Validators | None = None) -> Fetched:
        """Fetch ``url``; a dropped connection or a status in ``retry_statuses`` is retried."""
        for attempt in range(self.config.retries + 1):
            last = attempt == self.config.retries
            try:
                page = await self._get_once(url, _conditional_headers(validators))
            except _ConnectionFailedError:
                if last:
                    raise
            else:
                if last or page.status not in self.config.retry_statuses:
                    return page
            await self.sleep(self.config.retry_backoff_seconds * 2**attempt)
        raise FetchError("unreachable")  # not reached: the last attempt returns or raises

    async def _get_once(self, url: str, headers: dict[str, str]) -> Fetched:
        current = url
        for _ in range(self.config.max_redirects + 1):
            await self.check(current)
            try:
                async with self._session().get(
                    current,
                    allow_redirects=False,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response:
                    if response.status == HTTPStatus.NOT_MODIFIED:
                        return Fetched(current, response.status, "", b"")
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
                    return Fetched(
                        current,
                        response.status,
                        content_type,
                        bytes(body),
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                    )
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise _ConnectionFailedError(type(exc).__name__) from exc
        raise FetchError("too_many_redirects")

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
