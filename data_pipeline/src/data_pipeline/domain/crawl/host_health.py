from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from http import HTTPStatus

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
