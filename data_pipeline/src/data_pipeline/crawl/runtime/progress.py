from __future__ import annotations

import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from scrapy import signals
from scrapy.crawler import Crawler
from twisted.internet.task import LoopingCall

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


@dataclass(slots=True)
class EtaSample:
    at: float
    completed: int
    queued: int
    discovered: int


class EtaEstimator:
    def __init__(self, stable_samples: int = 3) -> None:
        self.stable_samples = stable_samples
        self.samples: deque[EtaSample] = deque(maxlen=stable_samples * 2)

    def observe(self, at: float, completed: int, queued: int, discovered: int) -> str:
        self.samples.append(EtaSample(at, completed, queued, discovered))
        return self.render()

    def render(self) -> str:
        if len(self.samples) < self.stable_samples:
            return "unknown (discovering)"

        window = list(self.samples)[-self.stable_samples :]
        if window[-1].discovered > window[0].discovered:
            return "unknown (discovering)"

        latest = window[-1]
        earliest = window[0]
        delta_completed = latest.completed - earliest.completed
        delta_time = latest.at - earliest.at

        if latest.queued == 0:
            return "0s"

        if delta_time <= 0 or delta_completed <= 0:
            return "unknown (idle)"

        recent_rate = delta_completed / delta_time
        remaining_seconds = latest.queued / recent_rate
        if remaining_seconds <= 0:
            return "0s"
        if remaining_seconds < _SECONDS_PER_MINUTE:
            return f"{int(remaining_seconds)}s"
        if remaining_seconds < _SECONDS_PER_HOUR:
            minutes = int(remaining_seconds // _SECONDS_PER_MINUTE)
            seconds = int(remaining_seconds % _SECONDS_PER_MINUTE)
            return f"{minutes}m {seconds}s"
        hours = int(remaining_seconds // _SECONDS_PER_HOUR)
        minutes = int((remaining_seconds % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE)
        return f"{hours}h {minutes}m"


class CrawlProgressReporter:
    def __init__(
        self,
        crawler: Crawler,
        *,
        output: TextIO = sys.stderr,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.crawler = crawler
        self.output = output
        self.clock = clock
        self.started = clock()
        self.eta_estimator = EtaEstimator(stable_samples=3)
        self._peak_queue: int = 0
        self._recent_rate: float = 0.0
        self._loop: LoopingCall | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> CrawlProgressReporter:
        reporter = cls(crawler)
        crawler.progress_reporter = reporter  # pyrefly: ignore[missing-attribute] -- read back by run/full.py
        interval = crawler.settings["HUST_TUNING"].progress_interval_seconds
        if interval > 0:
            loop = LoopingCall(reporter.report)
            reporter._loop = loop
            loop.start(interval, now=False)
            crawler.signals.connect(reporter.close, signal=signals.spider_closed)
        return reporter

    def close(self, *args: object) -> None:  # noqa: ARG002 -- signal handler arguments are ignored
        if self._loop is not None and self._loop.running:
            self._loop.stop()

    def _get(self, key: str, default: Any = 0) -> Any:
        stats = self.crawler.stats
        if stats is None:
            return default
        getter = getattr(stats, "get_value", None)
        return getter(key, default) if getter else stats.get(key, default)

    def metrics(self) -> dict[str, object]:
        if self._peak_queue == 0 and self._recent_rate == 0.0:
            enqueued = int(self._get("scheduler/enqueued", 0))
            dequeued = int(self._get("scheduler/dequeued", 0))
            self._peak_queue = max(0, enqueued - dequeued)
            elapsed = max(0.001, self.clock() - self.started)
            done = int(self._get("response_received_count", 0))
            self._recent_rate = done / elapsed
        return {
            "peak_queue": self._peak_queue,
            "recent_rate": self._recent_rate,
        }

    def report(self) -> str:
        elapsed = max(0.001, self.clock() - self.started)
        done = int(self._get("response_received_count", 0))
        enqueued = int(self._get("scheduler/enqueued", 0))
        dequeued = int(self._get("scheduler/dequeued", 0))
        queued = max(0, enqueued - dequeued)
        rate = done / elapsed
        self._peak_queue = max(self._peak_queue, queued)
        self._recent_rate = rate

        eta = self.eta_estimator.observe(
            at=self.clock(),
            completed=done,
            queued=queued,
            discovered=enqueued,
        )

        cpu = self._get("hust/resources/cpu_percent", None)
        ram = self._get("hust/resources/ram_percent", None)

        line = f"crawl | done={done} | queued={queued} | rate={rate:.1f} url/s"
        if cpu is not None:
            line += f" | cpu={float(cpu):.0f}%"
        if ram is not None:
            line += f" | ram={float(ram):.0f}%"
        line += f" | eta={eta}"

        print(line, file=self.output, flush=True)
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is not None:
            slots = downloader.slots
            transferring = sum(len(slot.transferring) for slot in slots.values())
            waiting = sum(len(slot.queue) for slot in slots.values())
            active = len(downloader.active)
            print(
                f"download | active={active} | transferring={transferring}"
                f" | slot_waiting={waiting}"
                f" | outside_slots={max(0, active - sum(len(s.active) for s in slots.values()))}"
                f" | retries={self._get('hust/retry/count', 0)}"
                f" | retry_waiting={self._get('hust/retry/waiting', 0)}"
                f" | exceptions={self._get('downloader/exception_count', 0)}",
                file=self.output,
                flush=True,
            )
            for key, slot in slots.items():
                if not slot.active:
                    continue
                print(
                    f"slot | host={key} | active={len(slot.active)}"
                    f" | transferring={len(slot.transferring)} | queued={len(slot.queue)}"
                    f" | concurrency={slot.concurrency} | delay={slot.delay:.2f}s",
                    file=self.output,
                    flush=True,
                )
        return line
