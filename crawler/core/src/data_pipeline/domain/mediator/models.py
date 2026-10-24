from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

type UrlKind = Literal["hub", "page", "document"]

DEFAULT_RECRAWL_DAYS: Mapping[UrlKind, float] = {"hub": 1.0, "page": 7.0, "document": 30.0}


@dataclass(frozen=True, slots=True)
class QueuedUrl:
    url: str
    host: str
    kind: UrlKind
    next_due_at: float | None
    last_sent_at: float | None
    attempts: int


@dataclass(frozen=True, slots=True)
class MediatorConfig:
    downloaders: tuple[str, ...]
    database: Path
    port: int = 5200
    host_delay_seconds: float = 1.0  # the slide's "thời gian chờ": the least time between two sends
    per_host_concurrency: int = 1
    autothrottle: bool = True
    autothrottle_start_delay_seconds: float = 0.25
    autothrottle_max_delay_seconds: float = 5.0
    autothrottle_target_concurrency: float = 4.0
    ping_timeout_seconds: float = 2.0
    load_timeout_seconds: float = 10.0
    node_cooldown_seconds: float = 30.0
    max_attempts: int = 5
    tick_seconds: float = 0.5
    recrawl_days: Mapping[UrlKind, float] = field(
        default_factory=lambda: dict(DEFAULT_RECRAWL_DAYS)
    )


@dataclass(frozen=True, slots=True)
class CrawlConfig:
    """The one-process crawl: ``workers`` in-process download nodes under the mediator's rules."""

    workers: int
    mediator: MediatorConfig
