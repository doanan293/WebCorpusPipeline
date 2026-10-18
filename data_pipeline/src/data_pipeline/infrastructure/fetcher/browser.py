from __future__ import annotations

import asyncio

from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from data_pipeline.domain.fetcher.models import Fetched, FetcherConfig, FetchError
from data_pipeline.infrastructure.fetcher.playwright_driver import isolate_playwright_driver


class PlaywrightRenderer:
    """The ``Renderer`` port: headless Chromium, started on first use, one page per render."""

    def __init__(self, config: FetcherConfig) -> None:
        self.config = config
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.lock = asyncio.Lock()

    async def _browser(self) -> Browser:
        async with self.lock:
            if self.browser is None:
                isolate_playwright_driver()
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True)
            return self.browser

    async def render(self, url: str) -> Fetched:
        try:
            browser = await self._browser()
            page = await browser.new_page(user_agent=self.config.user_agent)
            try:
                response = await page.goto(
                    url, wait_until="networkidle", timeout=self.config.timeout_seconds * 1000
                )
                html = await page.content()
                status = response.status if response is not None else 200
                return Fetched(page.url, status, "text/html; charset=utf-8", html.encode("utf-8"))
            finally:
                await page.close()
        except PlaywrightError as exc:
            raise FetchError("render_failed") from exc

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
            self.browser = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
