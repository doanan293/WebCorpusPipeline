from __future__ import annotations

from typing import Any

from data_pipeline.application.aggregator.ports import Outbox, ServiceClient, Target


async def deliver(
    target: Target, payload: dict[str, Any], *, client: ServiceClient, outbox: Outbox
) -> None:
    """Send now; what cannot be sent waits in the outbox instead of being lost."""
    if not await client.send(target, payload):
        outbox.append(target, payload)


async def flush_outbox(client: ServiceClient, outbox: Outbox) -> int:
    """Resend what waits in the outbox, oldest first; return how many went through."""
    sent: list[int] = []
    for entry in outbox.pending():
        if not await client.send(entry.target, entry.payload):
            break
        sent.append(entry.id)
    outbox.remove(sent)
    return len(sent)
