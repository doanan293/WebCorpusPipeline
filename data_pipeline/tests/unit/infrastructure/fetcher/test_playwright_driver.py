import asyncio
from typing import Any

import playwright._impl._transport as transport
import pytest

from data_pipeline.infrastructure.fetcher.playwright_driver import isolate_playwright_driver


def test_driver_starts_in_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_exec(*args: object, **kwargs: object) -> str:
        seen.update(kwargs)
        return "process"

    monkeypatch.setattr(transport, "asyncio", asyncio)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    isolate_playwright_driver()
    isolate_playwright_driver()
    result = asyncio.run(transport.asyncio.create_subprocess_exec("node", "driver"))

    assert result == "process"
    assert seen["start_new_session"] is True
    assert transport.asyncio.Queue is asyncio.Queue
