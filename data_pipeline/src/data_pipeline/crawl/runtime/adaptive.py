from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.http import Request, Response

from data_pipeline.crawl.domain.access import classify_access
from data_pipeline.crawl.runtime.resources import ResourceMonitor, ResourceSample

_FORBIDDEN_STREAK_LIMIT = 3


@dataclass(frozen=True, slots=True)
class Observation:
    at: float
    status: int | None
    error: str | None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class HostDecision:
    concurrency: int
    cooldown_until: float | None


class HostHealth:
    def __init__(
        self,
        *,
        start_concurrency: int,
        hard_ceiling: int,
        window_size: int = 20,
        change_interval: float = 30.0,
    ) -> None:
        if not 1 <= start_concurrency <= hard_ceiling:
            msg = "start concurrency must be within hard ceiling"
            raise ValueError(msg)
        self.current = start_concurrency
        self.hard_ceiling = hard_ceiling
        self.window_size = window_size
        self.change_interval = change_interval
        self._observations: deque[Observation] = deque(maxlen=window_size)
        self._cooldown_until: float | None = None
        self._last_change = float("-inf")
        self._rate_limit_streak = 0
        self._forbidden_streak = 0

    def observe(self, observation: Observation) -> None:
        self._observations.append(observation)
        if observation.status == HTTPStatus.TOO_MANY_REQUESTS:
            self.current = 1
            self._rate_limit_streak += 1
            retry_after = observation.retry_after_seconds
            if retry_after is None:
                retry_after = min(120.0, 30.0 * (2 ** (self._rate_limit_streak - 1)))
            retry_after = max(0.0, retry_after)
            self._cooldown_until = observation.at + retry_after
            self._last_change = observation.at
            self._forbidden_streak = 0
        elif observation.error == "captcha":
            self.current = 1
            self._rate_limit_streak = 0
            self._forbidden_streak = 0
            self._cooldown_until = observation.at + 300.0
            self._last_change = observation.at
        elif observation.status == HTTPStatus.FORBIDDEN:
            self._rate_limit_streak = 0
            self._forbidden_streak += 1
            if self._forbidden_streak >= _FORBIDDEN_STREAK_LIMIT:
                self.current = 1
                self._cooldown_until = observation.at + 300.0
                self._last_change = observation.at
        else:
            self._rate_limit_streak = 0
            self._forbidden_streak = 0
            if (
                observation.error in {"timeout", "transport"} or observation.status in {503, 504}
            ) and observation.at - self._last_change >= self.change_interval:
                new_value = max(1, self.current // 2)
                if new_value != self.current:
                    self.current = new_value
                    self._last_change = observation.at

    def recommend(self, now: float) -> HostDecision:
        if self._cooldown_until is not None and now < self._cooldown_until:
            return HostDecision(1, self._cooldown_until)
        healthy = len(self._observations) >= self.window_size and all(
            observation.error is None
            and observation.status is not None
            and HTTPStatus.OK <= observation.status < HTTPStatus.BAD_REQUEST
            for observation in self._observations
        )
        if healthy and now - self._last_change >= self.change_interval:
            self.current = min(self.hard_ceiling, self.current + 1)
            self._last_change = now
        cooldown = (
            self._cooldown_until
            if self._cooldown_until is not None and now < self._cooldown_until
            else None
        )
        return HostDecision(min(self.current, self.hard_ceiling), cooldown)


class AdaptiveConcurrencyMiddleware:
    """Feed response health back into Scrapy's existing downloader slots."""

    def __init__(
        self,
        crawler: Crawler,
        *,
        max_per_host: int,
        resource_limit_percent: int = 90,
    ) -> None:
        self._crawler = crawler
        self._max_per_host = max_per_host
        self._monitor = ResourceMonitor(
            threshold_percent=resource_limit_percent,
            on_pressure=self._count_pressure,
            on_sample=self._record_sample,
        )
        self._hosts: dict[str, HostHealth] = {}
        self._slot_delays: dict[str, float] = {}
        self._signal_observed_responses: set[int] = set()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> AdaptiveConcurrencyMiddleware:
        tuning = crawler.settings.get("HUST_TUNING")
        if tuning is None:
            msg = "HUST_TUNING is required for adaptive middleware"
            raise ValueError(msg)
        middleware = cls(
            crawler,
            max_per_host=int(tuning.max_per_host),
            resource_limit_percent=int(tuning.resource_limit_percent),
        )
        crawler.signals.connect(
            middleware._response_downloaded,
            signal=signals.response_downloaded,
        )
        crawler.signals.connect(middleware._spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(middleware._spider_closed, signal=signals.spider_closed)
        return middleware

    def _spider_opened(self, *args: object) -> None:  # noqa: ARG002 -- signal arguments are ignored
        self._monitor.start()

    def _spider_closed(self, *args: object) -> None:  # noqa: ARG002 -- signal arguments are ignored
        self._monitor.stop()

    def _record_sample(self, sample: ResourceSample) -> None:
        stats = getattr(self._crawler, "stats", None)
        if stats is None:
            return
        for key, value in (
            ("cpu_percent", sample.cpu_percent),
            ("ram_percent", sample.ram_percent),
        ):
            if value is not None:
                stats.set_value(f"hust/resources/{key}", value)

    def _count_pressure(self) -> None:
        stats = getattr(self._crawler, "stats", None)
        if stats is not None:
            stats.inc_value("hust/resources/pressure")

    @staticmethod
    def _host(request: Request) -> str:
        return (urlsplit(request.url).hostname or "").lower().rstrip(".")

    def _downloader_slots(self) -> dict[str, Any]:
        engine = self._crawler.engine
        return engine.downloader.slots if engine is not None else {}

    def _health(self, host: str, request: Request | None = None) -> HostHealth:
        health = self._hosts.get(host)
        if health is None:
            slot = None
            if request is not None:
                slot_key = request.meta.get("download_slot", host)
                slot = self._downloader_slots().get(slot_key)
            start = int(getattr(slot, "concurrency", 1) or 1)
            health = self._hosts[host] = HostHealth(
                start_concurrency=min(start, self._max_per_host),
                hard_ceiling=self._max_per_host,
            )
        return health

    def _observe_response(self, request: Request, response: Response) -> None:
        retry_after = None
        raw_retry_after = response.headers.get(b"Retry-After")
        if raw_retry_after:
            try:
                retry_after = max(0.0, float(raw_retry_after.decode("latin1").strip()))
            except ValueError:
                retry_after = None
        error = None
        content_type = (response.headers.get(b"Content-Type") or b"").decode("latin1").lower()
        if "html" in content_type and hasattr(response, "text"):
            access = classify_access(
                status=response.status,
                url=response.url,
                html=response.text,
                rendered=bool(request.meta.get("rendered")),
            )
            if access.outcome == "captcha_blocked":
                error = "captcha"
        self.observe(request, response.status, error, retry_after)
        self.reconcile_host(self._host(request), request)

    def _response_downloaded(
        self,
        request: Request,
        response: Response,
        spider: Spider | None = None,  # noqa: ARG002 -- Scrapy signal handler interface
    ) -> None:
        self._observe_response(request, response)
        self._signal_observed_responses.add(id(response))

    def process_response(self, request: Request, response: Response) -> Response:
        response_id = id(response)
        if response_id in self._signal_observed_responses:
            self._signal_observed_responses.remove(response_id)
        else:
            self._observe_response(request, response)
        return response

    def process_exception(self, request: Request, exception: Exception) -> None:
        error = "timeout" if "timeout" in exception.__class__.__name__.lower() else "transport"
        self.observe(request, None, error)
        self.reconcile_host(self._host(request), request)

    def observe(
        self,
        request: Request,
        status: int | None,
        error: str | None,
        retry_after_seconds: float | None = None,
    ) -> None:
        host = self._host(request)
        health = self._health(host, request)
        health.observe(
            Observation(
                at=monotonic(),
                status=status,
                error=error,
                retry_after_seconds=retry_after_seconds,
            )
        )
        stats = getattr(self._crawler, "stats", None)
        if stats is not None and hasattr(stats, "inc_value"):
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                stats.inc_value(f"hust/host/{host}/429")
            elif status in {503, 504}:
                stats.inc_value(f"hust/host/{host}/5xx")
            elif error:
                stats.inc_value(f"hust/host/{host}/{error}")

    def reconcile_host(
        self, host: str, request: Request | None = None, *, now: float | None = None
    ) -> None:
        health = self._health(host, request)
        current_time = monotonic() if now is None else now
        decision = health.recommend(current_time)
        if self._monitor.under_pressure():
            decision = HostDecision(1, decision.cooldown_until)

        slots = self._downloader_slots()
        slot_keys = {host}
        if request is not None:
            slot_keys.add(str(request.meta.get("download_slot", host)))
        for slot_key in slot_keys:
            slot = slots.get(slot_key)
            if slot is None:
                continue
            slot.concurrency = min(self._max_per_host, max(1, decision.concurrency))
            if decision.cooldown_until is not None:
                baseline = self._slot_delays.setdefault(slot_key, float(slot.delay))
                remaining = max(0.0, decision.cooldown_until - current_time)
                slot.delay = max(baseline, remaining)
            elif slot_key in self._slot_delays:
                slot.delay = self._slot_delays.pop(slot_key)
