from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import aiohttp

from crawler.application.storage.ports import ImageFetchError, ImageResult
from crawler.domain.storage.pictures import MAX_PICTURE_BYTES
from crawler.infra.shared.network import (
    NonPublicAddressError,
    resolve_public_hostname,
    validate_public_web_url,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_CHUNK = 65536
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 10

type Check = Callable[[str], Awaitable[None]]


async def public_address_check(url: str) -> None:
    """Refuse URLs that are not public web addresses, by literal IP or by what DNS answers.

    Image URLs come from crawled pages, so one may point at a private or loopback service.
    """
    decision = validate_public_web_url(url)
    if not decision.accepted or decision.hostname is None:
        raise ImageFetchError(decision.reason or "malformed_url")
    port = urlsplit(url).port or (443 if url.startswith("https") else 80)
    try:
        await resolve_public_hostname(decision.hostname, port)
    except NonPublicAddressError as exc:
        raise ImageFetchError("non_public_address") from exc
    except OSError as exc:
        raise ImageFetchError("dns_failed") from exc


class AiohttpImageGetter:
    """The ``ImageGetter`` port: one GET per image, reading at most ``MAX_PICTURE_BYTES + 1``.

    Redirects are followed here so that every hop passes ``check``.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        connections: int = 256,
        check: Check = public_address_check,
    ) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.connections = connections
        self.check = check
        self.session: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                connector=aiohttp.TCPConnector(limit=self.connections),
            )
        return self.session

    async def get(self, url: str) -> ImageResult:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self.check(current)
            try:
                try:
                    result = await self._get_once(current, verify=True)
                except aiohttp.ClientConnectorCertificateError:
                    # Some hosts leave out the intermediate certificate, which browsers fetch and
                    # aiohttp does not. The picture is public, and the address check still holds.
                    result = await self._get_once(current, verify=False)
            except TimeoutError as exc:
                raise ImageFetchError("timeout") from exc
            except (aiohttp.ClientError, ValueError, UnicodeError) as exc:
                raise ImageFetchError("unreachable") from exc
            if isinstance(result, str):
                current = result
                continue
            return result
        raise ImageFetchError("too_many_redirects")

    async def _get_once(self, url: str, *, verify: bool) -> ImageResult | str:
        """The response to one GET, or the URL a redirect points to."""
        async with self._session().get(
            url, timeout=self.timeout, allow_redirects=False, ssl=verify
        ) as response:
            location = response.headers.get("Location")
            if response.status in _REDIRECTS and location:
                return urljoin(url, location)
            body = bytearray()
            async for chunk in response.content.iter_chunked(_CHUNK):
                body += chunk
                if len(body) > MAX_PICTURE_BYTES:
                    body = body[: MAX_PICTURE_BYTES + 1]
                    break
            content_type = response.headers.get("Content-Type", "")
            return ImageResult(response.status, content_type, bytes(body))

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
