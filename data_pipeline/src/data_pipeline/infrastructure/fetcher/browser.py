from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

from data_pipeline.domain.fetcher.models import Fetched, FetcherConfig, FetchError
from data_pipeline.infrastructure.fetcher.http_getter import public_address_check
from data_pipeline.infrastructure.fetcher.playwright_driver import isolate_playwright_driver

log = logging.getLogger(__name__)

_REDIRECTS = frozenset({301, 302, 303, 307, 308})


async def _allow_any(_: str) -> None:
    return None


class PlaywrightRenderer:
    """The ``Renderer`` port: headless Chromium, started on first use, one page per render.

    Every request the page makes (the page, its redirects, images, scripts, ``fetch()``) passes
    ``check`` first and is aborted when it fails, and WebSockets are refused, so a page cannot reach
    the blocks' own network or other private addresses. At most ``browser_pages`` pages are open at
    once.
    """

    def __init__(
        self, config: FetcherConfig, check: Callable[[str], Awaitable[None]] | None = None
    ) -> None:
        self.config = config
        self.check = check or (
            _allow_any if config.allow_private_addresses else public_address_check
        )
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.lock = asyncio.Lock()
        self.pages = asyncio.Semaphore(config.browser_pages)

    async def _browser(self) -> Browser:
        async with self.lock:
            if self.browser is None:
                isolate_playwright_driver()
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True)
            return self.browser

    async def _allowed(self, route: Route, url: str) -> bool:
        try:
            await self.check(url)
        except FetchError as exc:
            log.info("render: blocked %s (%s)", url, exc)
            await route.abort("blockedbyclient")
            return False
        return True

    async def _fetch_checked(self, route: Route) -> None:
        """Answer a subresource ourselves, checking every redirect hop before following it."""
        current = route.request.url
        for _ in range(self.config.max_redirects + 1):
            response = await route.fetch(url=current, max_redirects=0)
            location = response.headers.get("location")
            if response.status not in _REDIRECTS or not location:
                await route.fulfill(response=response)
                return
            current = urljoin(current, location)
            if not await self._allowed(route, current):
                return
        await route.abort("failed")

    async def render(self, url: str) -> Fetched:
        async with self.pages:
            try:
                browser = await self._browser()
                page = await browser.new_page(user_agent=self.config.user_agent)
                try:
                    return await self._render(page, url)
                finally:
                    await page.close()
            except PlaywrightError as exc:
                raise FetchError("render_failed") from exc

    async def _render(self, page: Page, url: str) -> Fetched:
        # Chromium follows a redirect of an answered request without asking the route again, so
        # a redirect of the page itself is stopped here and navigated to only after its check.
        redirect: list[str] = []

        async def guard(route: Route) -> None:
            request = route.request
            if not await self._allowed(route, request.url):
                return
            try:
                if request.is_navigation_request() and request.frame == page.main_frame:
                    response = await route.fetch(max_redirects=0)
                    location = response.headers.get("location")
                    if response.status in _REDIRECTS and location:
                        redirect.append(urljoin(request.url, location))
                        await route.abort("aborted")
                        return
                    await route.fulfill(response=response)
                    return
                await self._fetch_checked(route)
            except PlaywrightError:
                await route.abort("failed")

        await page.route("**/*", guard)
        # A crawler needs no WebSocket, and a socket is not an HTTP request the guard sees.
        await page.route_web_socket("**/*", lambda socket: socket.close())
        target = url
        for _ in range(self.config.max_redirects + 1):
            await self.check(target)
            redirect.clear()
            try:
                response = await page.goto(
                    target, wait_until="networkidle", timeout=self.config.timeout_seconds * 1000
                )
            except PlaywrightError:
                if not redirect:
                    raise
                target = redirect[0]
                continue
            if response is None:
                raise FetchError("render_failed")
            await self.check(page.url)
            body = (await page.content()).encode("utf-8")
            if len(body) > self.config.max_response_bytes:
                raise FetchError("too_large")
            return Fetched(page.url, response.status, "text/html; charset=utf-8", body)
        raise FetchError("too_many_redirects")

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
            self.browser = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
