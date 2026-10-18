from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from data_pipeline.application.aggregator.ports import OutboxEntry, Target
from data_pipeline.infrastructure.shared.files import atomic_write_text


class JsonlOutbox:
    """The ``Outbox`` port: one JSON line per waiting request, rewritten when entries leave."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, target: Target, payload: dict[str, Any]) -> None:
        entries = self.pending()
        next_id = max((entry.id for entry in entries), default=0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": next_id, "target": target, "payload": payload}) + "\n")

    def pending(self) -> list[OutboxEntry]:
        if not self.path.exists():
            return []
        entries: list[OutboxEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw = json.loads(line)
                entries.append(OutboxEntry(raw["id"], raw["target"], raw["payload"]))
        return entries

    def remove(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        gone = set(ids)
        kept = [entry for entry in self.pending() if entry.id not in gone]
        atomic_write_text(
            self.path,
            "".join(
                json.dumps({"id": e.id, "target": e.target, "payload": e.payload}) + "\n"
                for e in kept
            ),
        )
