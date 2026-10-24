from __future__ import annotations

from dataclasses import dataclass

from data_pipeline.domain.mediator.models import MediatorConfig


@dataclass(slots=True)
class _Host:
    delay: float
    in_flight: int = 0
    last_sent: float = float("-inf")


class HostThrottle:
    """How hard each host may be hit: requests in flight and the time between sends.

    With AutoThrottle on, the time between sends follows how long the host takes to answer (as
    Scrapy's AutoThrottle does): a slow host is given more room, a fast one less, never below
    ``host_delay_seconds`` nor above ``autothrottle_max_delay_seconds``.

    The latency is the whole time a node is busy with the URL (download, and for the in-process
    crawl also reading and storing it), as that is all the slide lets the mediator see. A slow
    reader or an unreachable Mongo therefore also slows the hosts, up to the maximum delay.
    """

    def __init__(self, config: MediatorConfig) -> None:
        self.config = config
        self.hosts: dict[str, _Host] = {}

    def _start_delay(self) -> float:
        if not self.config.autothrottle:
            return self.config.host_delay_seconds
        return max(self.config.host_delay_seconds, self.config.autothrottle_start_delay_seconds)

    def _host(self, host: str) -> _Host:
        if host not in self.hosts:
            self.hosts[host] = _Host(self._start_delay())
        return self.hosts[host]

    def delay(self, host: str) -> float:
        return self._host(host).delay

    def blocked(self, now: float) -> frozenset[str]:
        """Hosts that may not be sent to at ``now``."""
        return frozenset(
            name
            for name, state in self.hosts.items()
            if state.in_flight >= self.config.per_host_concurrency
            or now < state.last_sent + state.delay
        )

    def next_unblock(self, now: float) -> float | None:
        """When the earliest host now held back only by its wait may be sent to again."""
        times = [
            state.last_sent + state.delay
            for state in self.hosts.values()
            if state.in_flight < self.config.per_host_concurrency
            and now < state.last_sent + state.delay
        ]
        return min(times, default=None)

    def started(self, host: str, now: float) -> None:
        state = self._host(host)
        state.in_flight += 1
        state.last_sent = now

    def finished(self, host: str, latency: float | None) -> None:
        """One request to ``host`` ended; ``latency`` is how long it took, when known."""
        state = self._host(host)
        state.in_flight = max(0, state.in_flight - 1)
        if latency is None or not self.config.autothrottle:
            return
        target = latency / self.config.autothrottle_target_concurrency
        delay = max(target, (state.delay + target) / 2)
        state.delay = min(
            max(delay, self.config.host_delay_seconds), self.config.autothrottle_max_delay_seconds
        )
