"""In-memory stand-ins for the downloader ports."""

from __future__ import annotations

from crawler.domain.downloader.models import Fetched, FetchError
from crawler.domain.shared.validators import Validators


class FakeGetter:
    """Answers ``get`` from ``pages``; a missing URL raises ``FetchError("unreachable")``."""

    def __init__(self, pages: dict[str, Fetched]) -> None:
        self.pages = pages
        self.validators: dict[str, Validators | None] = {}

    async def get(self, url: str, validators: Validators | None = None) -> Fetched:
        self.validators[url] = validators
        if url not in self.pages:
            raise FetchError("unreachable")
        return self.pages[url]


class FakeRenderer:
    def __init__(self, pages: dict[str, Fetched] | None = None) -> None:
        self.pages = pages or {}
        self.rendered: list[str] = []

    async def render(self, url: str) -> Fetched:
        self.rendered.append(url)
        if url not in self.pages:
            raise FetchError("render_failed")
        return self.pages[url]

    async def close(self) -> None:
        return None


class FakeReaderClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[Fetched, str]] = []
        self.requested: list[str] = []
        self.not_modified: list[str] = []

    async def send(self, page: Fetched, fmt: str, *, requested_url: str) -> bool:
        self.sent.append((page, fmt))
        self.requested.append(requested_url)
        return self.ok

    async def send_not_modified(self, requested_url: str) -> bool:
        self.not_modified.append(requested_url)
        return self.ok
