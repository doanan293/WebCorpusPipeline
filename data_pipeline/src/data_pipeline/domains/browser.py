from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlparse

from data_pipeline.core.policies.network import resolve_hostname
from data_pipeline.domains.probe import (
    ProbeAttempt,
    ProbeResult,
    classify_response,
    is_target_web_url,
)


async def _bounded_close(resource, *, timeout: float = 3.0) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(resource.close(), timeout=timeout)


async def _block_web_socket(web_socket) -> None:
    await web_socket.close(code=1008, reason="audit network policy")


def _browser_attempt_needs_retry(attempt: ProbeAttempt) -> bool:
    if attempt.status is None:
        return True
    _, reason = classify_response(
        attempt.status,
        attempt.body,
        attempt.content_type,
        attempt.final_url,
    )
    return reason == "javascript_shell"


def _browser_request_allowed(url: str, *, timeout: float) -> bool:
    policy_key = _browser_policy_key(url)
    if policy_key is None:
        return False
    _, hostname, _ = policy_key
    try:
        resolve_hostname(hostname, timeout=min(timeout, 5.0))
    except OSError:
        return False
    return True


def _browser_policy_key(url: str) -> tuple[str, str, int] | None:
    if not is_target_web_url(url):
        return None
    parsed = urlparse(url)
    return (
        parsed.scheme,
        (parsed.hostname or "").lower(),
        parsed.port or (80 if parsed.scheme == "http" else 443),
    )


def browser_probe_results(
    results: list[ProbeResult],
    *,
    timeout: float,
    workers: int,
) -> dict[str, ProbeAttempt]:
    async def render() -> dict[str, ProbeAttempt]:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415 -- playwright is an optional 'crawl' extra
        from playwright.async_api import async_playwright  # noqa: PLC0415 -- playwright is an optional 'crawl' extra

        attempts: dict[str, ProbeAttempt] = {}
        semaphore = asyncio.Semaphore(workers)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                ignore_https_errors=True,
                service_workers="block",
            )
            browser_host_policy: dict[tuple[str, str, int], bool] = {}

            async def block_heavy_resources(route) -> None:
                if route.request.resource_type in {"font", "image", "media"}:
                    await route.abort()
                    return
                policy_key = _browser_policy_key(route.request.url)
                if policy_key is None:
                    await route.abort()
                    return
                allowed = browser_host_policy.get(policy_key)
                if allowed is None:
                    allowed = await asyncio.to_thread(
                        _browser_request_allowed,
                        route.request.url,
                        timeout=timeout,
                    )
                    if allowed:
                        browser_host_policy[policy_key] = True
                if allowed:
                    await route.continue_()
                    return
                await route.abort()

            await context.route("**/*", block_heavy_resources)

            await context.route_web_socket("**/*", _block_web_socket)

            async def render_one(result: ProbeResult) -> None:
                async with semaphore:
                    for _ in range(2):
                        page = await context.new_page()
                        response = None
                        navigation_error: str | None = None
                        try:
                            try:
                                response = await page.goto(
                                    result.final_url or f"https://{result.hostname}/",
                                    wait_until="domcontentloaded",
                                    timeout=timeout * 1000,
                                )
                            except PlaywrightTimeoutError as exc:
                                navigation_error = f"PlaywrightTimeoutError: {exc}"
                            await page.wait_for_timeout(2000)
                            body = (await page.content()).encode()
                            attempts[result.hostname] = ProbeAttempt(
                                status=response.status if response else result.status,
                                final_url=page.url,
                                content_type="text/html",
                                body=body,
                                error=navigation_error,
                            )
                        except Exception as exc:  # noqa: BLE001 -- a browser failure is recorded as that host's attempt
                            attempts[result.hostname] = ProbeAttempt(
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        finally:
                            await _bounded_close(page)
                        if not _browser_attempt_needs_retry(attempts[result.hostname]):
                            break

            await asyncio.gather(*(render_one(result) for result in results))
            await _bounded_close(context)
            await _bounded_close(browser)
        return attempts

    return asyncio.run(render())
