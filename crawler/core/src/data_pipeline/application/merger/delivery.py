from __future__ import annotations

from typing import Any

from data_pipeline.application.merger.ports import Outbox, ServiceClient, Target


async def deliver(
    target: Target, payload: dict[str, Any], *, client: ServiceClient, outbox: Outbox
) -> None:
    """Send now; what cannot be sent waits in the outbox, what is refused goes to dead letters."""
    result = await client.send(target, payload)
    if result == "retry":
        outbox.append(target, payload)
    elif result == "dead":
        outbox.dead(target, payload, "rejected")


async def flush_outbox(client: ServiceClient, outbox: Outbox) -> int:
    """Resend what waits, oldest first per block; return how many went through.

    A block that is still down keeps its entries for the next round without holding up the other
    block's; an entry refused for good moves to the dead letters instead of blocking the rest.
    """
    sent = 0
    done: list[int] = []
    down: set[Target] = set()
    for entry in outbox.pending():
        if entry.target in down:
            continue
        result = await client.send(entry.target, entry.payload)
        if result == "retry":
            down.add(entry.target)
            continue
        if result == "dead":
            outbox.dead(entry.target, entry.payload, "rejected")
        else:
            sent += 1
        done.append(entry.id)
    outbox.remove(done)
    return sent
