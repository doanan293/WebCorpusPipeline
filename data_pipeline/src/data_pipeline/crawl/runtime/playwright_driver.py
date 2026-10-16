from __future__ import annotations

import asyncio
import types
from typing import Any, override

import playwright._impl._transport as transport


class _DriverAsyncio(types.ModuleType):
    """``asyncio`` as Playwright's transport sees it, except the driver gets its own session."""

    @override
    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def create_subprocess_exec(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("start_new_session", True)
        return await asyncio.create_subprocess_exec(*args, **kwargs)


def isolate_playwright_driver() -> None:
    """Keep Ctrl-C away from the Playwright driver process.

    Ctrl-C reaches every process of the terminal's foreground group. If the node driver dies
    with it, renders in flight never finish and Scrapy's graceful shutdown waits forever.
    In its own session the driver only stops when Scrapy closes the browser, or when its
    stdin pipe closes because this process exited.
    """
    if not isinstance(transport.asyncio, _DriverAsyncio):
        # Replaces the module's own reference only; asyncio stays untouched for everyone else.
        vars(transport)["asyncio"] = _DriverAsyncio("asyncio")
