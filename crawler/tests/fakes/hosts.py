"""In-memory stand-ins for the domains ports."""

from __future__ import annotations

from collections.abc import Callable

from crawler.domain.hosts.models import ProbeAttempt, ProbeResult


class FakeProber:
    def __init__(self, probe: Callable[[str], ProbeResult]) -> None:
        self._probe = probe

    def probe(self, hostname: str) -> ProbeResult:
        return self._probe(hostname)


class FakeBrowser:
    """Calls ``probe(shells)``; records the hostnames of each batch it is given."""

    def __init__(self, probe: Callable[[list[ProbeResult]], dict[str, ProbeAttempt]]) -> None:
        self._probe = probe
        self.calls: list[list[str]] = []

    def probe(self, shells: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        self.calls.append([shell.hostname for shell in shells])
        return self._probe(shells)


class MemoryReports:
    """Candidates given up front; the audit's outputs kept in memory."""

    def __init__(self, *candidates: str) -> None:
        self.candidates = tuple(sorted(candidates))
        self.all_hostnames: list[str] = []
        self.active: list[str] = []
        self.results: list[ProbeResult] = []

    def load_candidates(self) -> tuple[str, ...]:
        return self.candidates

    def write(
        self, all_hostnames: list[str], active: list[str], results: list[ProbeResult]
    ) -> None:
        self.all_hostnames = all_hostnames
        self.active = sorted(set(active))
        self.results = results
