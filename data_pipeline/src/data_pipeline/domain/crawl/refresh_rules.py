from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus

from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.extract.fingerprint import change_fingerprint


@dataclass(frozen=True, slots=True)
class RefreshSettings:
    pagination_empty_pages: int = 1
    recent_days: int = 60
    recent_runs: int = 4
    recheck_limit: int = 5000

    @classmethod
    def from_config(cls, config: CrawlerConfig) -> RefreshSettings:
        return cls(
            pagination_empty_pages=config.refresh_pagination_empty_pages,
            recent_days=config.refresh_recent_days,
            recent_runs=config.refresh_recent_runs,
            recheck_limit=config.refresh_recheck_limit,
        )


def conditional_headers(record: Mapping[str, object]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if record.get("etag"):
        headers["If-None-Match"] = str(record["etag"])
    if record.get("last_modified"):
        headers["If-Modified-Since"] = str(record["last_modified"])
    return headers


def response_validators(header: Callable[[bytes], object]) -> dict[str, str]:
    """The ETag and Last-Modified of a response, given a header lookup by name."""
    values: dict[str, str] = {}
    for name, key in ((b"ETag", "etag"), (b"Last-Modified", "last_modified")):
        raw = header(name)
        if raw:
            values[key] = raw.decode("latin1") if isinstance(raw, bytes) else str(raw)
    return values


# Targets refetched as hubs: their own text changes with every new post and is not news.
HUB_REASONS = frozenset({"homepage", "navigation", "pagination_head", "pagination_next"})
GONE_STATUSES = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.GONE})


def classify_refetch(previous_text: str | None, text: str, gate_reason: str | None) -> str:
    """Classify a content page refetch by comparing change fingerprints of its two texts."""
    if gate_reason is not None:
        return f"gate_{gate_reason}"
    if previous_text is None:
        return "new"
    if change_fingerprint(previous_text) == change_fingerprint(text):
        return "unchanged"
    return "modified"
