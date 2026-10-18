from __future__ import annotations

import asyncio
import logging
import unicodedata
from dataclasses import dataclass

from data_pipeline.application.controller.ports import Clock, Downloader, UrlQueue
from data_pipeline.domain.controller.models import ControllerConfig, QueuedUrl
from data_pipeline.domain.controller.rotation import NodeRotation
from data_pipeline.domain.controller.scheduling import after_failure, after_success
from data_pipeline.domain.controller.throttle import HostThrottle
from data_pipeline.domain.shared.protocol import BUSY, IDLE

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Assignment:
    url: str
    host: str
    started_at: float


def _normalized(status: str | None) -> str | None:
    # A node typed on another system may send "đang chờ" decomposed (NFD); it is the same text.
    return unicodedata.normalize("NFC", status) if status is not None else None


class Dispatcher:
    """Sends due URLs to idle download nodes, one URL per step.

    A node that took a URL is "đang bận" until it is done; the time until it says "đang chờ"
    again is how long the host took, which sets the host's pace (``HostThrottle``).
    """

    def __init__(
        self, queue: UrlQueue, downloader: Downloader, clock: Clock, config: ControllerConfig
    ) -> None:
        self.queue = queue
        self.downloader = downloader
        self.clock = clock
        self.config = config
        self.rotation = NodeRotation(config.downloaders)
        self.throttle = HostThrottle(config)
        self.assignments: dict[str, _Assignment] = {}

    def in_flight(self) -> int:
        """URLs handed to a node that has not finished them yet."""
        return len(self.assignments)

    async def _refresh(self, now: float) -> None:
        """Ask the nodes holding a URL whether they are done."""
        for node, job in list(self.assignments.items()):
            status = _normalized(await self.downloader.ping(node))
            if status == BUSY:
                continue
            del self.assignments[node]
            if status is None:
                self.throttle.finished(job.host, None)
                self.rotation.rest(node, now + self.config.node_cooldown_seconds)
            else:
                self.throttle.finished(job.host, now - job.started_at)

    async def step(self) -> bool:
        """Send one due URL; ``True`` when a node accepted it."""
        now = self.clock.now()
        await self._refresh(now)
        item = self.queue.next_due(now, self.throttle.blocked(now))
        if item is None:
            return False
        for node in self.rotation.order(now):
            if node in self.assignments:
                continue
            status = _normalized(await self.downloader.ping(node))
            if status is None:
                self.rotation.rest(node, now + self.config.node_cooldown_seconds)
                continue
            if status != IDLE:
                if status != BUSY:
                    # A slide node answers "đang chờ" or "đang bận"; say what came instead.
                    log.warning("unexpected ping status from %s: %r", node, status)
                continue
            self.rotation.used(node)
            result = await self.downloader.load(node, item.url)
            if result == "busy":
                continue  # taken by someone else between ping and load; try the next node
            if result == "accepted":
                self._sent(node, item, now)
                return True
            self._failed(node, item, now)
            return False
        return False

    def _sent(self, node: str, item: QueuedUrl, now: float) -> None:
        due = after_success(now, item.kind, self.config.recrawl_days)
        self.queue.mark_sent(item.url, now, due)
        self.assignments[node] = _Assignment(item.url, item.host, now)
        self.throttle.started(item.host, now)
        log.info("load %s -> %s: ok", item.url, node)

    def _failed(self, node: str, item: QueuedUrl, now: float) -> None:
        self.rotation.rest(node, now + self.config.node_cooldown_seconds)
        due, attempts = after_failure(item, now, self.config)
        self.queue.mark_failed(item.url, attempts, due)
        log.info("load %s -> %s: failed (attempt %d)", item.url, node, item.attempts + 1)
        if attempts == 0:
            log.warning("giving up on %s until %s", item.url, due)


async def run_dispatch(dispatcher: Dispatcher, tick_seconds: float) -> None:
    """Keep sending due URLs; wait a tick whenever nothing could be sent."""
    while True:
        try:
            sent = await dispatcher.step()
        except Exception:
            log.exception("dispatch step failed")
            sent = False
        if not sent:
            await asyncio.sleep(tick_seconds)
