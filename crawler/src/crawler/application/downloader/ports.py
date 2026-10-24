from __future__ import annotations

from typing import Protocol

from crawler.domain.downloader.models import Fetched


class PageGetter(Protocol):
    async def get(self, url: str) -> Fetched:
        """Download one URL, following redirects; raise ``FetchError`` when it cannot."""
        ...


class Renderer(Protocol):
    async def render(self, url: str) -> Fetched:
        """Load the URL in a headless browser and return the HTML after scripts ran."""
        ...

    async def close(self) -> None: ...


class ReaderClient(Protocol):
    async def send(self, page: Fetched, fmt: str) -> bool:
        """Hand the downloaded data to the reader block's ``/parse``."""
        ...
