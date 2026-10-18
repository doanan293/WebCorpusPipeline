from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AggregatorConfig:
    port: int = 3300
    hosts_file: Path = Path("config/domains/active.txt")
    allowed_suffixes: tuple[str, ...] = ("hust.edu.vn",)
    batch_size: int = 500
    batch_seconds: float = 1.0
    outbox_retry_seconds: float = 30.0
    database: Path = Path("data/state/aggregator.sqlite3")
    outbox: Path = Path("data/state/outbox.jsonl")
