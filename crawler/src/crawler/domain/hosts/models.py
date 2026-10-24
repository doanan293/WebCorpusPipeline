from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    hostname: str
    active: bool
    reason: str
    scheme: str | None = None
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    content_digest: str | None = None
    browser_rendered: bool = False
    error: str | None = None
    checked_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class ProbeAttempt:
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    body: bytes = b""
    error: str | None = None


class UnsafeRedirectError(OSError):
    def __init__(self, target_url: str, reason: str) -> None:
        super().__init__(f"{reason}: {target_url}")
        self.target_url = target_url
        self.reason = reason
