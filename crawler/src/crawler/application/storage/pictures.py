"""``crawler pictures fetch``: download the images of ``picture`` records as base64.

Hosts are fetched side by side; each host gets at most ``per_host`` downloads at once, spaced by
its own delay. A host answering 429/503 or timing out has its delay doubled (up to
``max_delay_seconds``) and the image tried again (three attempts); each success brings the delay
back down towards ``start_delay_seconds``. Every image is saved as soon as it is done, so an
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

from crawler.application.storage.ports import (
    ImageFetchError,
    ImageGetter,
    ImageResult,
    PictureStore,
)
from crawler.domain.storage.pictures import (
    MAX_PICTURE_BYTES,
    encode_picture,
    picture_format,
    sniff_picture,
)

log = logging.getLogger(__name__)

ATTEMPTS = 3
_BACK_OFF = frozenset({HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE})
_PROGRESS_EVERY = 1000
_UNTYPED = frozenset({"", "application/octet-stream", "binary/octet-stream"})


@dataclass(frozen=True, slots=True)
class PictureFetchSettings:
    workers: int = 64
    per_host: int = 2
    start_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0
    retry_errors: bool = False


@dataclass(slots=True)
class _Host:
    delay: float
    next_at: float = 0.0


def _is_image(result: ImageResult) -> bool:
    mime = result.content_type.partition(";")[0].strip().lower()
    if mime.startswith("image/"):
        return True
    return mime in _UNTYPED and sniff_picture(result.body) != ""


def _verdict(result: ImageResult) -> str | None:
    """``None`` for a usable image, else the reason kept in ``error``."""
    if result.status != HTTPStatus.OK:
        return f"http_{result.status}"
    if not result.body:
        return "empty"
    if not _is_image(result):
        return "not_image"
    if len(result.body) > MAX_PICTURE_BYTES:
        return "too_large"
    return None


async def fetch_pictures(
    store: PictureStore,
    getter: ImageGetter,
    *,
    now_iso: str,
    settings: PictureFetchSettings = PictureFetchSettings(),  # noqa: B008 -- frozen, so shared safely
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Counter[str]:
    """Fetch every pending picture; return counts by outcome (``ok`` or the error)."""
    queues: dict[str, deque[str]] = defaultdict(deque)
    for url in store.pending(retry_errors=settings.retry_errors):
        queues[urlsplit(url).hostname or ""].append(url)
    total = sum(len(queue) for queue in queues.values())
    log.info("pictures: %d to fetch on %d hosts", total, len(queues))
    counts: Counter[str] = Counter()
    limit = asyncio.Semaphore(settings.workers)
    hosts = {name: _Host(settings.start_delay_seconds) for name in queues}

    async def wait_turn(host: _Host) -> None:
        now = clock()
        start = max(now, host.next_at)
        host.next_at = start + host.delay
        if start > now:
            await sleep(start - now)

    def slower(host: _Host) -> None:
        host.delay = min(host.delay * 2, settings.max_delay_seconds)

    def faster(host: _Host) -> None:
        host.delay = max(host.delay / 2, settings.start_delay_seconds)

    async def one(url: str, host: _Host) -> str:
        error = "unreachable"
        for _ in range(ATTEMPTS):
            await wait_turn(host)
            async with limit:
                try:
                    result = await getter.get(url)
                except ImageFetchError as exc:
                    error = str(exc) or "unreachable"
                    slower(host)
                    continue
            if result.status in _BACK_OFF:
                error = f"http_{result.status}"
                slower(host)
                continue
            faster(host)
            error = _verdict(result) or ""
            if not error:
                store.save_picture(
                    url,
                    b64=encode_picture(result.body),
                    fmt=picture_format(result.content_type, url) or sniff_picture(result.body),
                    size=len(result.body),
                    error=None,
                    fetched_at=now_iso,
                )
                return "ok"
            break
        store.save_picture(
            url, b64="", fmt=picture_format("", url), size=None, error=error, fetched_at=now_iso
        )
        return error

    async def drain(name: str) -> None:
        queue, host = queues[name], hosts[name]
        while queue:
            outcome = await one(queue.popleft(), host)
            counts[outcome] += 1
            done = sum(counts.values())
            if done % _PROGRESS_EVERY == 0:
                log.info("pictures: %d/%d done (%d ok)", done, total, counts["ok"])

    await asyncio.gather(*(drain(name) for name in queues for _ in range(settings.per_host)))
    return counts
