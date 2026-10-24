from __future__ import annotations

import aiohttp

from crawler.application.storage.ports import ImageFetchError, ImageResult
from crawler.domain.storage.pictures import MAX_PICTURE_BYTES

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_CHUNK = 65536


class AiohttpImageGetter:
    """The ``ImageGetter`` port: one GET per image, reading at most ``MAX_PICTURE_BYTES + 1``."""

    def __init__(self, timeout_seconds: float = 30.0, connections: int = 256) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.connections = connections
        self.session: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                connector=aiohttp.TCPConnector(limit=self.connections),
            )
        return self.session

    async def get(self, url: str) -> ImageResult:
        try:
            async with self._session().get(url, timeout=self.timeout) as response:
                body = bytearray()
                async for chunk in response.content.iter_chunked(_CHUNK):
                    body += chunk
                    if len(body) > MAX_PICTURE_BYTES:
                        body = body[: MAX_PICTURE_BYTES + 1]
                        break
                content_type = response.headers.get("Content-Type", "")
                return ImageResult(response.status, content_type, bytes(body))
        except TimeoutError as exc:
            raise ImageFetchError("timeout") from exc
        except (aiohttp.ClientError, ValueError, UnicodeError) as exc:
            raise ImageFetchError("unreachable") from exc

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
