from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from crawler.application.merger.aggregate import Scope, aggregate
from crawler.application.merger.delivery import deliver, flush_outbox
from crawler.application.merger.ports import (
    MergerState,
    Outbox,
    ServiceClient,
    Target,
)
from crawler.domain.merger.batching import UrlBatcher
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import Seeds

log = logging.getLogger(__name__)


def _ignore(_: str) -> None:
    return None


class MergerRuntime:
    """The merger block's work, whichever way it runs (HTTP service or one process).

    ``handle`` takes one record; ``tick`` and ``flush`` are the background rounds (send a due
    batch of new URLs, resend the outbox) and never raise; ``shutdown`` keeps the URLs still
    waiting in the batcher, which are already marked seen, by putting them in the outbox.
    """

    def __init__(
        self,
        config: MergerConfig,
        state: MergerState,
        outbox: Outbox,
        client: ServiceClient,
        scope: Scope,
        *,
        clock: Callable[[], float],
        count: Callable[[str], None] = _ignore,
    ) -> None:
        self.state = state
        self.outbox = outbox
        self.client = client
        self.scope = scope
        self.clock = clock
        self.count = count
        self.batcher = UrlBatcher(config.batch_size, config.batch_seconds)

    async def send(self, target: Target, payload: dict[str, Any]) -> None:
        self.count(f"to_{target}")
        await deliver(target, payload, client=self.client, outbox=self.outbox)

    async def handle(self, record: dict[str, Any]) -> None:
        await aggregate(
            record,
            seen=self.state,
            owners=self.state,
            deliver=self.send,
            batcher=self.batcher,
            scope=self.scope,
            now=self.clock(),
        )

    async def seed(self, urls: Sequence[str]) -> None:
        if urls:
            await self.send("mediator", {"urls": list(urls)})
            log.info("seeded %d start urls", len(urls))

    def adopt_initial_scope(self, hosts: frozenset[str]) -> None:
        """Take ``hosts`` as the scope when the store has none yet (the crawl before seeds)."""
        if not self.state.scope_hosts():
            self.state.add_scope_hosts(hosts)
        self._reload_scope()

    async def add_seeds(self, seeds: Seeds) -> None:
        """Widen the scope to the hosts of the seeds, then queue them (known URLs keep
        their schedule)."""
        for problem in seeds.problems:
            log.warning("seeds: %s", problem)
        self.state.add_scope_hosts(seeds.hosts)
        self._reload_scope()
        await self.seed(seeds.urls)

    def _reload_scope(self) -> None:
        self.scope = replace(self.scope, allowed_hosts=self.state.scope_hosts())

    async def tick(self) -> None:
        batch: list[str] | None = None
        try:
            batch = self.batcher.due(self.clock())
            if batch:
                await self.send("mediator", {"urls": batch})
        except asyncio.CancelledError:
            # Stopped mid-send: these URLs are already seen, so keep them for the next start.
            if batch:
                self.outbox.append("mediator", {"urls": batch})
            raise
        except Exception:
            log.exception("sending new urls failed")

    async def flush(self) -> None:
        try:
            if sent := await flush_outbox(self.client, self.outbox):
                log.info("outbox: resent %d requests", sent)
        except Exception:
            log.exception("resending the outbox failed")

    async def shutdown(self) -> None:
        if self.batcher.pending:
            self.outbox.append("mediator", {"urls": list(self.batcher.pending)})
            log.info("kept %d new urls in the outbox", len(self.batcher.pending))
            self.batcher.pending = []
            self.batcher.started = None
