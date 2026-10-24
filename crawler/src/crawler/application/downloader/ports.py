from __future__ import annotations

from typing import Protocol

from crawler.domain.downloader.models import Fetched, Validators


class PageGetter(Protocol):
    async def get(self, url: str, validators: Validators | None = None) -> Fetched:
        """Download one URL, following redirects; raise ``FetchError`` when it cannot.

        With ``validators`` the request is conditional: an unchanged page answers 304, no body.
        """
        ...


class Renderer(Protocol):
    async def render(self, url: str) -> Fetched:
        """Load the URL in a headless browser and return the HTML after scripts ran."""
        ...

    async def close(self) -> None: ...


class ReaderClient(Protocol):
    async def send(self, page: Fetched, fmt: str, *, requested_url: str) -> bool:
        """Hand the downloaded data to the reader block's ``/parse``."""
        ...

    async def send_not_modified(self, requested_url: str) -> bool:
        """Tell the reader block the site answered 304 for ``requested_url``."""
        ...
