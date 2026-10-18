from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from data_pipeline.application.aggregator.aggregate import Scope, aggregate
from data_pipeline.application.aggregator.delivery import deliver, flush_outbox
from data_pipeline.application.aggregator.ports import (
    AggregatorState,
    Outbox,
    ServiceClient,
    Target,
)
from data_pipeline.domain.aggregator.batching import UrlBatcher
from data_pipeline.domain.aggregator.config import AggregatorConfig

log = logging.getLogger(__name__)


def _ignore(_: str) -> None:
    return None


class AggregatorRuntime:
    """The aggregator block's work, whichever way it runs (HTTP service or one process).

    ``handle`` takes one record; ``tick`` and ``flush`` are the background rounds (send a due
    batch of new URLs, resend the outbox) and never raise; ``shutdown`` keeps the URLs still
    waiting in the batcher, which are already marked seen, by putting them in the outbox.
    """

    def __init__(
        self,
        config: AggregatorConfig,
        state: AggregatorState,
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
            await self.send("controller", {"urls": list(urls)})
            log.info("seeded %d start urls", len(urls))

    async def tick(self) -> None:
        try:
            batch = self.batcher.due(self.clock())
            if batch:
                await self.send("controller", {"urls": batch})
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
            self.outbox.append("controller", {"urls": list(self.batcher.pending)})
            log.info("kept %d new urls in the outbox", len(self.batcher.pending))
            self.batcher.pending = []
            self.batcher.started = None
