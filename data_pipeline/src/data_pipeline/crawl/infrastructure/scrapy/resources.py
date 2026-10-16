from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from twisted.internet.task import LoopingCall

_CPU_IDLE_FIELD = 3
_CPU_IOWAIT_FIELD = 4


@dataclass(frozen=True, slots=True)
class ResourceSample:
    cpu_percent: float | None
    ram_percent: float | None


class ResourceSampler:
    def __init__(
        self,
        *,
        read_text: Callable[[Path], str] | None = None,
        meminfo_text: Callable[[], str] | None = None,
    ) -> None:
        self._read_text = read_text or (lambda path: path.read_text(encoding="utf-8"))
        self._meminfo_text = meminfo_text or (
            lambda: Path("/proc/meminfo").read_text(encoding="utf-8")
        )
        self._previous_cpu: tuple[int, int] | None = None

    def sample(self) -> ResourceSample:
        return ResourceSample(self._cpu_percent(), self._ram_percent())

    def _cpu_percent(self) -> float | None:
        try:
            fields = self._read_text(Path("/proc/stat")).splitlines()[0].split()
            values = [int(value) for value in fields[1:]]
            total = sum(values)
            idle = values[_CPU_IDLE_FIELD] + (
                values[_CPU_IOWAIT_FIELD] if len(values) > _CPU_IOWAIT_FIELD else 0
            )
        except (OSError, IndexError, ValueError):
            return None
        previous = self._previous_cpu
        self._previous_cpu = (total, idle)
        if previous is None or total <= previous[0]:
            return None
        total_delta = total - previous[0]
        idle_delta = max(0, idle - previous[1])
        return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)

    def _ram_percent(self) -> float | None:
        try:
            values = {}
            for line in self._meminfo_text().splitlines():
                key, separator, raw = line.partition(":")
                if separator and key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(raw.strip().split()[0])
            total, available = values["MemTotal"], values["MemAvailable"]
            if total <= 0:
                return None
            return round(max(0.0, min(100.0, 100.0 * (total - available) / total)), 1)
        except (OSError, KeyError, ValueError, IndexError):
            return None


class ResourceMonitor:
    """Sample CPU and RAM on a timer; report pressure only when it lasts.

    Each sample averages CPU over the interval since the previous one, so a single busy
    moment cannot throttle the whole crawl.
    """

    def __init__(
        self,
        *,
        interval_seconds: float = 5.0,
        threshold_percent: float = 90.0,
        sustained_samples: int = 2,
        sampler: ResourceSampler | None = None,
        on_pressure: Callable[[], None] | None = None,
        on_sample: Callable[[ResourceSample], None] | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.threshold_percent = threshold_percent
        self.sustained_samples = sustained_samples
        self._sampler = sampler or ResourceSampler()
        self._on_pressure = on_pressure
        self._on_sample = on_sample
        self._high_streak = 0
        self._pressure = False
        self._loop: LoopingCall | None = None

    def sample(self) -> None:
        reading = self._sampler.sample()
        if self._on_sample is not None:
            self._on_sample(reading)
        high = any(
            value is not None and value >= self.threshold_percent
            for value in (reading.cpu_percent, reading.ram_percent)
        )
        self._high_streak = self._high_streak + 1 if high else 0
        was_under_pressure = self._pressure
        self._pressure = self._high_streak >= self.sustained_samples
        if self._pressure and not was_under_pressure and self._on_pressure is not None:
            self._on_pressure()

    def under_pressure(self) -> bool:
        return self._pressure

    def start(self) -> None:
        self._sampler.sample()  # prime the CPU counters so the first timed sample is an average
        self._loop = LoopingCall(self.sample)
        self._loop.start(self.interval_seconds, now=False)

    def stop(self) -> None:
        if self._loop is not None and self._loop.running:
            self._loop.stop()
