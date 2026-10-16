from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from data_pipeline.crawl.domain.frontier.frontier import Candidate, DiscoverySource, Purpose
from data_pipeline.crawl.domain.seeds import SeedMode


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A fetched response with the request metadata the crawl rules need."""

    url: str
    input_url: str
    status: int
    content_type: str
    body: bytes
    encoding: str | None
    purpose: Purpose
    seed_mode: SeedMode
    discovery_source: DiscoverySource
    meta: Mapping[str, Any]
    validators: Mapping[str, str] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    # Decodes the body the way the HTTP library would; None for a non-text response.
    decode: Callable[[], str] | None = None

    def text(self) -> str:
        if self.decode is not None:
            return self.decode()
        return self.body.decode(self.encoding or "utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class FailedRequest:
    """A request that failed before a response arrived."""

    url: str
    input_url: str
    seed_mode: SeedMode
    purpose: Purpose
    request_scope: str
    discovery_source: DiscoverySource
    explicit: bool
    http_fallback: bool
    refresh_reason: str | None
    error: str
    error_type: str
    ignored: bool  # the request was dropped on purpose (IgnoreRequest), not by the network


@dataclass(frozen=True, slots=True)
class Scheduled:
    """A candidate the frontier accepted: fetch ``url`` with ``priority``."""

    candidate: Candidate
    url: str
    priority: int
    http_fallback: bool = False


@dataclass(frozen=True, slots=True)
class RefreshFetch:
    """Refetch a known page with conditional headers during a refresh run."""

    url: str
    reason: str


type FollowUp = Scheduled | RefreshFetch
