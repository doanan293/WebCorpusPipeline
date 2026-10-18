from __future__ import annotations

import logging

from data_pipeline.application.controller.ports import Clock, Downloader, UrlQueue
from data_pipeline.domain.controller.models import ControllerConfig
from data_pipeline.domain.controller.rotation import NodeRotation
from data_pipeline.domain.controller.scheduling import after_failure, after_success
from data_pipeline.domain.shared.protocol import BUSY, IDLE

log = logging.getLogger(__name__)


class Dispatcher:
    """Sends due URLs to idle download nodes, one URL per step."""

    def __init__(
        self, queue: UrlQueue, downloader: Downloader, clock: Clock, config: ControllerConfig
    ) -> None:
        self.queue = queue
        self.downloader = downloader
        self.clock = clock
        self.config = config
        self.rotation = NodeRotation(config.downloaders)

    async def step(self) -> bool:
        """Send one due URL; ``True`` when a node accepted it."""
        now = self.clock.now()
        item = self.queue.next_due(now, self.config.host_delay_seconds)
        if item is None:
            return False
        for node in self.rotation.order(now):
            status = await self.downloader.ping(node)
            if status is None:
                self.rotation.rest(node, now + self.config.node_cooldown_seconds)
                continue
            if status != IDLE:
                if status != BUSY:
                    # A slide node answers "đang chờ" or "đang bận"; say what came instead.
                    log.warning("unexpected ping status from %s: %r", node, status)
                continue
            self.rotation.used(node)
            if await self.downloader.load(node, item.url):
                due = after_success(now, item.kind, self.config.recrawl_days)
                self.queue.mark_sent(item.url, now, due)
                log.info("load %s -> %s: ok", item.url, node)
                return True
            self.rotation.rest(node, now + self.config.node_cooldown_seconds)
            due, attempts = after_failure(item, now, self.config)
            self.queue.mark_failed(item.url, attempts, due)
            log.info("load %s -> %s: failed (attempt %d)", item.url, node, item.attempts + 1)
            if attempts == 0:
                log.warning("giving up on %s until %s", item.url, due)
            return False
        return False
