from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from data_pipeline.application.aggregator.ports import OutboxEntry, Target
from data_pipeline.infrastructure.shared.files import atomic_write_text

log = logging.getLogger(__name__)


def _line(entry_id: int, target: Target, payload: dict[str, Any]) -> str:
    return json.dumps({"id": entry_id, "target": target, "payload": payload}) + "\n"


class JsonlOutbox:
    """The ``Outbox`` port: one JSON line per waiting request, rewritten when entries leave.

    Requests refused for good go to ``<name>.dead.jsonl`` beside it. A line torn by a kill
    mid-write is skipped (and dropped at the next rewrite) instead of breaking every later read.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.dead_path = path.with_name(f"{path.stem}.dead{path.suffix}")
        self.next_id: int | None = None

    def append(self, target: Target, payload: dict[str, Any]) -> None:
        if self.next_id is None:
            self.next_id = max((entry.id for entry in self.pending()), default=0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            if handle.tell() > 0:
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")  # end a line torn by a kill so the new one stands alone
            handle.write(_line(self.next_id, target, payload).encode("utf-8"))
        self.next_id += 1

    def dead(self, target: Target, payload: dict[str, Any], reason: str) -> None:
        self.dead_path.parent.mkdir(parents=True, exist_ok=True)
        with self.dead_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"target": target, "reason": reason, "payload": payload}) + "\n"
            )
        log.warning(
            "outbox: %s refused %s for good (%s)", target, payload.get("url", "urls"), reason
        )

    def pending(self) -> list[OutboxEntry]:
        if not self.path.exists():
            return []
        entries: list[OutboxEntry] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                entries.append(OutboxEntry(raw["id"], raw["target"], raw["payload"]))
            except (ValueError, KeyError, TypeError):
                log.warning("outbox: skipping unreadable line %d of %s", number, self.path)
        return entries

    def remove(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        gone = set(ids)
        kept = [entry for entry in self.pending() if entry.id not in gone]
        atomic_write_text(self.path, "".join(_line(e.id, e.target, e.payload) for e in kept))
