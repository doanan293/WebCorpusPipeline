from __future__ import annotations

from importlib.metadata import version

import aiohttp

from data_pipeline.domain.enrich.models import HttpResponse
from data_pipeline.domain.shared.config import Settings

USER_AGENT = f"data-pipeline/{version('data-pipeline')}"


class PublicTransport:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_read=self.settings.timeout_seconds,
            connect=self.settings.timeout_seconds,
        )
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            auto_decompress=False,
        )
        return self

    async def __aexit__(self, *exc: object):
        if self.session:
            await self.session.close()

    async def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        if self.session is None:
            msg = "PublicTransport must be used as an async context manager"
            raise RuntimeError(msg)
        response = await self.session.get(
            url, headers=headers, allow_redirects=True, max_redirects=self.settings.max_redirects
        )

        async def close() -> None:
            response.release()

        # The CIMultiDictProxy itself, so header lookups stay case-insensitive.
        return HttpResponse(
            response.status,
            response.headers,
            str(response.url),
            response.content.iter_chunked(self.settings.chunk_bytes),
            close,
        )
