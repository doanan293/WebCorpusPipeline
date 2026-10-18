from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AggregatorConfig:
    port: int = 3300
    seeds_file: Path = Path("config/domains/active.txt")  # one host or URL per line
    # Hosts of the crawl so far; adopted as the scope once, when the store has none yet.
    initial_scope_file: Path = Path("config/domains/active.txt")
    batch_size: int = 500
    batch_seconds: float = 1.0
    outbox_retry_seconds: float = 30.0
    database: Path = Path("data/state/aggregator.sqlite3")
    outbox: Path = Path("data/state/outbox.jsonl")
