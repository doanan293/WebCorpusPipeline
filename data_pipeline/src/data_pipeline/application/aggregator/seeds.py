from __future__ import annotations

from collections.abc import Iterable


def seed_payload(hosts: Iterable[str]) -> dict[str, list[str]]:
    """The controller's ``/add`` body for the start page of every host."""
    return {"urls": [f"https://{host}/" for host in hosts]}
