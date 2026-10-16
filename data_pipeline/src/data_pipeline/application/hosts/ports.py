from __future__ import annotations

from typing import Protocol

from data_pipeline.domain.hosts.models import ProbeAttempt, ProbeResult


class HostProber(Protocol):
    def probe(self, hostname: str) -> ProbeResult: ...


class BrowserProber(Protocol):
    def probe(self, shells: list[ProbeResult]) -> dict[str, ProbeAttempt]: ...


class AuditReports(Protocol):
    """Where the audit reads its candidates and writes its three outputs."""

    def load_candidates(self) -> tuple[str, ...]: ...

    def write(
        self, all_hostnames: list[str], active: list[str], results: list[ProbeResult]
    ) -> None: ...
