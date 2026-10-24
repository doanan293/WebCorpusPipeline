from __future__ import annotations

from http import HTTPStatus

import aiohttp

from crawler.application.mediator.ports import LoadResult
from crawler.domain.shared.validators import Validators


class HttpDownloader:
    """The ``Downloader`` port over the slide's ``GET /ping`` and ``POST /load`` of a node."""

    def __init__(self, *, ping_timeout: float, load_timeout: float) -> None:
        self.ping_timeout = ping_timeout
        self.load_timeout = load_timeout
        self.session: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def ping(self, node: str) -> str | None:
        try:
            async with self._session().get(
                f"{node}/ping", timeout=aiohttp.ClientTimeout(total=self.ping_timeout)
            ) as response:
                if not response.ok:
                    return None
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None
        status = body.get("status") if isinstance(body, dict) else None
        return status if isinstance(status, str) else None

    async def load(self, node: str, url: str, validators: Validators | None = None) -> LoadResult:
        body: dict[str, str] = {"url": url}
        if validators is not None and validators.etag:
            body["etag"] = validators.etag
        if validators is not None and validators.last_modified:
            body["last_modified"] = validators.last_modified
        try:
            async with self._session().post(
                f"{node}/load",
                json=body,
                timeout=aiohttp.ClientTimeout(total=self.load_timeout),
            ) as response:
                if response.status == HTTPStatus.CONFLICT:
                    return "busy"
                return "accepted" if response.ok else "failed"
        except (aiohttp.ClientError, TimeoutError):
            return "failed"

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
