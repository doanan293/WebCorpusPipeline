"""In-memory stand-ins for the fetcher ports."""

from __future__ import annotations

from data_pipeline.domain.fetcher.models import Fetched, FetchError


class FakeGetter:
    """Answers ``get`` from ``pages``; a missing URL raises ``FetchError("unreachable")``."""

    def __init__(self, pages: dict[str, Fetched]) -> None:
        self.pages = pages

    async def get(self, url: str) -> Fetched:
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


class FakeParserClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[Fetched, str]] = []

    async def send(self, page: Fetched, fmt: str) -> bool:
        self.sent.append((page, fmt))
        return self.ok
