from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReaderConfig:
    port: int = 6600
    llamaparse: bool = False
    cache_dir: Path = Path("data/enrichment/cache")
    cloud_poll_seconds: float = 5.0
    cloud_timeout_seconds: float = 1800.0
    document_workers: int = 8
