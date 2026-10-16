import asyncio
from typing import Any, cast

import pytest

from data_pipeline.domains import browser as domain_browser
from data_pipeline.domains.probe import (
    ProbeAttempt,
)
from data_pipeline.shared.infrastructure.network import NonPublicAddressError


def test_browser_resource_close_has_a_hard_timeout() -> None:
    class HangingResource:
        async def close(self) -> None:
            await asyncio.Event().wait()

    asyncio.run(domain_browser._bounded_close(HangingResource(), timeout=0.01))


def test_browser_web_sockets_are_closed_by_network_policy() -> None:
    class FakeWebSocket:
        closed_with: tuple[int, str] | None = None

        async def close(self, *, code: int, reason: str) -> None:
            self.closed_with = (code, reason)

    web_socket = FakeWebSocket()
    asyncio.run(domain_browser._block_web_socket(cast(Any, web_socket)))

    assert web_socket.closed_with == (1008, "audit network policy")


def test_browser_retries_only_an_unrendered_javascript_shell() -> None:
    shell = ProbeAttempt(
        status=200,
        final_url="https://example.hust.edu.vn/",
        content_type="text/html",
        body=b"<title>Portal</title><div id='root'></div>",
    )
    public = ProbeAttempt(
        status=200,
        final_url="https://example.hust.edu.vn/",
        content_type="text/html",
        body=b"<title>Portal</title><main><h1>Public news</h1></main>",
    )

    assert domain_browser._browser_attempt_needs_retry(shell) is True
    assert domain_browser._browser_attempt_needs_retry(public) is False


def test_browser_request_policy_blocks_external_and_non_public_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[str] = []

    def resolve(hostname: str, timeout: float = 5.0) -> None:
        resolved.append(hostname)
        if hostname == "private.hust.edu.vn":
            msg = "private target"
            raise NonPublicAddressError(msg)

    monkeypatch.setattr(domain_browser, "resolve_hostname", resolve)

    assert domain_browser._browser_request_allowed("https://public.hust.edu.vn/app.js", timeout=1)
    assert not domain_browser._browser_request_allowed("https://private.hust.edu.vn/api", timeout=1)
    assert not domain_browser._browser_request_allowed("https://example.com/tracker.js", timeout=1)
    assert not domain_browser._browser_request_allowed(
        "https://public.hust.edu.vn:2375/admin", timeout=1
    )
    assert resolved == ["public.hust.edu.vn", "private.hust.edu.vn"]


def test_browser_policy_cache_key_includes_scheme_and_effective_port() -> None:
    assert domain_browser._browser_policy_key("https://public.hust.edu.vn/app.js") == (
        "https",
        "public.hust.edu.vn",
        443,
    )
    assert domain_browser._browser_policy_key("http://public.hust.edu.vn/") == (
        "http",
        "public.hust.edu.vn",
        80,
    )
    assert domain_browser._browser_policy_key("https://public.hust.edu.vn:2375/admin") is None
