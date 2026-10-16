from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599
MAX_PERCENT = 100

if TYPE_CHECKING:
    from data_pipeline.crawl.domain.tuning import RuntimeTuning


def validate_network_contact(contact: str) -> None:
    """The contact is sent as the HTTP From header, so it must be a real email address."""
    normalized = contact.strip().lower()
    local, at, domain = normalized.partition("@")
    if not (local and at and "." in domain) or " " in normalized or ".invalid" in normalized:
        msg = "CRAWLER_CONTACT must be the operator's email address"
        raise ValueError(msg)


_REQUIRED_SETTINGS = (
    "concurrent_requests",
    "browser_user_agent",
    "concurrent_per_host",
    "download_delay_seconds",
    "download_timeout_seconds",
    "max_response_bytes",
    "cookies_enabled",
    "redirect_max_times",
    "autothrottle_enabled",
    "autothrottle_target_concurrency",
    "throttle_start_seconds",
    "throttle_max_seconds",
    "retry_times",
    "retry_statuses",
    "retry_backoff_base_seconds",
    "retry_max_delay_seconds",
    "playwright_max_pages",
    "resource_limit_percent",
    "assets",
    "max_file_bytes",
    "max_total_file_bytes",
    "pagination_empty_pages",
    "max_urls_per_host",
    "max_total_urls",
    "progress_interval_seconds",
    "refresh_pagination_empty_pages",
    "refresh_recent_days",
    "refresh_recent_runs",
    "refresh_recheck_limit",
    "store_html",
)
_NON_POSITIVE_SETTINGS = frozenset(
    {
        "browser_user_agent",
        "cookies_enabled",
        "autothrottle_enabled",
        "assets",
        "retry_statuses",
        "store_html",
    }
)
# Zero is a valid delay: per-host concurrency and AutoThrottle pace requests instead.
_NON_NEGATIVE_SETTINGS = frozenset({"download_delay_seconds"})
_POSITIVE_SETTINGS = tuple(
    name
    for name in _REQUIRED_SETTINGS
    if name not in _NON_POSITIVE_SETTINGS and name not in _NON_NEGATIVE_SETTINGS
)


@dataclass(frozen=True, slots=True)
class CrawlerConfig:
    hostnames: frozenset[str]
    contact: str
    concurrent_requests: int = 64
    concurrent_per_host: int = 4
    playwright_max_pages: int = 8
    download_delay_seconds: float = 0.0
    throttle_start_seconds: float = 0.25
    throttle_max_seconds: float = 5.0
    retry_times: int = 3
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)
    retry_backoff_base_seconds: float = 2.0
    retry_max_delay_seconds: float = 3600.0
    download_timeout_seconds: float = 60.0
    max_response_bytes: int = 100 * 1024 * 1024
    browser_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )
    cookies_enabled: bool = False
    redirect_max_times: int = 20
    autothrottle_enabled: bool = True
    autothrottle_target_concurrency: float = 4.0
    resource_limit_percent: int = 90
    assets: Literal["content-only", "all"] = "content-only"
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_file_bytes: int = 100 * 1024**3
    pagination_empty_pages: int = 3
    max_urls_per_host: int = 100_000
    max_total_urls: int = 1_000_000
    progress_interval_seconds: float = 10.0
    refresh_pagination_empty_pages: int = 1
    refresh_recent_days: int = 60
    refresh_recent_runs: int = 4
    refresh_recheck_limit: int = 5000
    store_html: bool = True

    @classmethod
    def load(
        cls,
        config_path: Path,
        *,
        hostnames: frozenset[str],
        environ: Mapping[str, str] | None = None,
    ) -> CrawlerConfig:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        environment = os.environ if environ is None else environ
        contact = environment.get("CRAWLER_CONTACT", "").strip()
        if not contact:
            msg = "CRAWLER_CONTACT is required (set it in .env)"
            raise ValueError(msg)
        validate_network_contact(contact)
        unknown = sorted(set(raw) - set(_REQUIRED_SETTINGS))
        if unknown:
            msg = f"unknown crawler settings: {', '.join(unknown)}"
            raise ValueError(msg)
        missing = sorted(name for name in _REQUIRED_SETTINGS if name not in raw)
        if missing:
            msg = f"missing required crawler settings: {', '.join(missing)}"
            raise ValueError(msg)

        def required_bool(name: str) -> bool:
            value = raw[name]
            if not isinstance(value, bool):
                msg = f"{name} must be a boolean"
                raise ValueError(msg)  # noqa: TRY004 -- invalid configuration is a ValueError
            return value

        config = cls(
            hostnames=frozenset(hostnames),
            contact=contact,
            browser_user_agent=str(raw["browser_user_agent"]),
            concurrent_requests=int(raw["concurrent_requests"]),
            concurrent_per_host=int(raw["concurrent_per_host"]),
            playwright_max_pages=int(raw["playwright_max_pages"]),
            download_delay_seconds=float(raw["download_delay_seconds"]),
            throttle_start_seconds=float(raw["throttle_start_seconds"]),
            throttle_max_seconds=float(raw["throttle_max_seconds"]),
            retry_times=int(raw["retry_times"]),
            retry_statuses=tuple(int(status) for status in raw["retry_statuses"]),
            retry_backoff_base_seconds=float(raw["retry_backoff_base_seconds"]),
            retry_max_delay_seconds=float(raw["retry_max_delay_seconds"]),
            download_timeout_seconds=float(raw["download_timeout_seconds"]),
            max_response_bytes=int(raw["max_response_bytes"]),
            cookies_enabled=required_bool("cookies_enabled"),
            redirect_max_times=int(raw["redirect_max_times"]),
            autothrottle_enabled=required_bool("autothrottle_enabled"),
            autothrottle_target_concurrency=float(raw["autothrottle_target_concurrency"]),
            resource_limit_percent=int(raw["resource_limit_percent"]),
            assets=raw["assets"],
            max_file_bytes=int(raw["max_file_bytes"]),
            max_total_file_bytes=int(raw["max_total_file_bytes"]),
            pagination_empty_pages=int(raw["pagination_empty_pages"]),
            max_urls_per_host=int(raw["max_urls_per_host"]),
            max_total_urls=int(raw["max_total_urls"]),
            progress_interval_seconds=float(raw["progress_interval_seconds"]),
            refresh_pagination_empty_pages=int(raw["refresh_pagination_empty_pages"]),
            refresh_recent_days=int(raw["refresh_recent_days"]),
            refresh_recent_runs=int(raw["refresh_recent_runs"]),
            refresh_recheck_limit=int(raw["refresh_recheck_limit"]),
            store_html=required_bool("store_html"),
        )
        config._validate()
        return config

    def _validate(self) -> None:
        for name in _NON_NEGATIVE_SETTINGS:
            if getattr(self, name) < 0:
                msg = f"{name} must not be negative"
                raise ValueError(msg)
        for name in _POSITIVE_SETTINGS:
            if getattr(self, name) <= 0:
                msg = f"{name} must be positive"
                raise ValueError(msg)
        if not self.retry_statuses or any(
            status < MIN_HTTP_STATUS or status > MAX_HTTP_STATUS for status in self.retry_statuses
        ):
            msg = "retry_statuses must contain HTTP status codes"
            raise ValueError(msg)
        if (
            self.throttle_start_seconds <= 0
            or self.throttle_max_seconds <= 0
            or self.throttle_start_seconds > self.throttle_max_seconds
        ):
            msg = "throttle delays must be positive and ordered"
            raise ValueError(msg)
        if self.autothrottle_target_concurrency > self.concurrent_per_host:
            msg = "autothrottle_target_concurrency must not exceed concurrent_per_host"
            raise ValueError(msg)
        if not 1 <= self.resource_limit_percent <= MAX_PERCENT:
            msg = "resource_limit_percent must be between 1 and 100"
            raise ValueError(msg)
        if self.assets not in {"content-only", "all"}:
            msg = "assets must be content-only or all"
            raise ValueError(msg)


_RUNTIME_CONFIG_KEYS = {
    "concurrent_requests",
    "concurrent_per_host",
    "playwright_max_pages",
    "download_delay_seconds",
    "throttle_start_seconds",
    "throttle_max_seconds",
    "autothrottle_enabled",
    "autothrottle_target_concurrency",
    "download_timeout_seconds",
    "retry_times",
    "retry_backoff_base_seconds",
    "retry_max_delay_seconds",
    "resource_limit_percent",
    "progress_interval_seconds",
    "refresh_pagination_empty_pages",
    "refresh_recent_days",
    "refresh_recent_runs",
    "refresh_recheck_limit",
    "store_html",
}


def _normalized_snapshot(values: dict[str, object]) -> dict[str, object]:
    def normalize(value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (set, frozenset)):
            items: list[Any] = [normalize(item) for item in value]
            return sorted(items)
        if isinstance(value, tuple):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        return value

    return {key: normalize(value) for key, value in sorted(values.items())}


def semantic_config_snapshot(config: CrawlerConfig) -> dict[str, object]:
    """Return the configuration fields that define phase identity."""

    values = asdict(config)
    for key in (*_RUNTIME_CONFIG_KEYS, "contact"):
        values.pop(key, None)
    return _normalized_snapshot(values)


def runtime_config_snapshot(config: CrawlerConfig, tuning: RuntimeTuning) -> dict[str, object]:
    """Return the active performance knobs recorded for a run."""

    values = asdict(config)
    values = {key: values[key] for key in _RUNTIME_CONFIG_KEYS}
    values.update(asdict(tuning))
    return _normalized_snapshot(values)
