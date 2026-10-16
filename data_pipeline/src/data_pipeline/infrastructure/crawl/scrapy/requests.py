from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response

from data_pipeline.application.crawl.ports import UrlRecords
from data_pipeline.domain.crawl.frontier.assets import recognizable_asset
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.pages import (
    FailedRequest,
    FetchedPage,
    FollowUp,
    RefreshFetch,
    Scheduled,
)
from data_pipeline.domain.crawl.refresh_rules import conditional_headers, response_validators


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def fetched_page(response: Response, *, purpose: str = "page") -> FetchedPage:
    """The parts of a Scrapy response the crawl rules read (``purpose`` when meta has none)."""
    meta = response.meta
    decode: Callable[[], str] | None = None
    if hasattr(response, "text"):

        def decode() -> str:
            return response.text

    return FetchedPage(
        url=response.url,
        input_url=meta.get("input_url", response.url),
        status=response.status,
        content_type=(response.headers.get(b"Content-Type") or b"").decode(
            "latin1", errors="replace"
        ),
        body=response.body,
        encoding=getattr(response, "encoding", None),
        purpose=meta.get("response_purpose", purpose),
        seed_mode=meta.get("seed_mode", "exact"),
        discovery_source=meta.get("discovery_source", "seed"),
        meta=meta,
        validators=response_validators(response.headers.get),
        flags=tuple(getattr(response, "flags", None) or ()),
        decode=decode,
        request_url=response.request.url if response.request is not None else None,
    )


def failed_request(failure: Any, *, purpose: str = "page") -> FailedRequest:
    """The parts of a failed Scrapy request the crawl rules read (``purpose`` if meta has none)."""
    request = failure.request
    meta = request.meta
    return FailedRequest(
        url=request.url,
        input_url=meta.get("input_url", request.url),
        seed_mode=meta.get("seed_mode", "exact"),
        purpose=meta.get("response_purpose", purpose),
        request_scope=meta.get("request_scope", "page"),
        discovery_source=meta.get("discovery_source", "seed"),
        explicit=meta.get("explicit", False),
        http_fallback=bool(meta.get("http_fallback")),
        refresh_reason=meta.get("refresh_reason"),
        error=str(failure.value),
        error_type=getattr(failure.type, "__name__", ""),
        ignored=isinstance(failure.value, IgnoreRequest),
        meta=meta,
    )


class RequestFactory:
    """Builds Scrapy requests for the fetches the crawl pipeline schedules."""

    def __init__(
        self,
        *,
        options: CrawlOptions,
        urls: UrlRecords,
        callback: Callable[..., Any],
        errback: Callable[..., Any],
    ) -> None:
        self.options = options
        self.urls = urls
        self.callback = callback
        self.errback = errback

    def build(self, follow: FollowUp) -> scrapy.Request:
        if isinstance(follow, RefreshFetch):
            return self.refresh(follow.url, follow.reason)
        return self.scheduled(follow)

    def scheduled(self, scheduled: Scheduled) -> scrapy.Request:
        canonical = scheduled.url
        candidate = scheduled.candidate
        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({_host(canonical)}),
            "input_url": canonical,
            "seed_mode": candidate.source_mode,
            "explicit": candidate.explicit,
            "response_purpose": candidate.purpose,
            "discovery_source": candidate.discovery_source,
            "discovered_from": candidate.discovered_from,
            "handle_httpstatus_all": True,
            "request_scope": "asset" if candidate.purpose == "asset" else "page",
            "asset_mode": self.options.assets,
            "link_kind": candidate.link_kind,
            "family_key": candidate.family_key,
            "ordinal": candidate.ordinal,
        }
        if candidate.purpose == "asset":
            meta.update(
                {
                    "asset_group": candidate.asset_group
                    or recognizable_asset(canonical)
                    or "document",
                    "asset_role": candidate.asset_role,
                    "referring_page": candidate.discovered_from,
                }
            )
        if scheduled.http_fallback:
            meta["http_fallback"] = True
        return scrapy.Request(
            canonical,
            callback=self.callback,
            errback=self.errback,
            priority=scheduled.priority,
            meta=meta,
            dont_filter=True,
        )

    def refresh(self, url: str, reason: str) -> scrapy.Request:
        """A conditional refetch of a known page during a refresh run."""
        record = self.urls.url_record(url) or {}
        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({_host(url)}),
            "input_url": url,
            "seed_mode": "exact" if record.get("seed_type") == "exact" else "recursive",
            "explicit": bool(record.get("explicit", False)),
            "response_purpose": str(record.get("response_purpose") or "page"),
            "discovery_source": record.get("discovery_source") or "seed",
            "discovered_from": record.get("discovered_from"),
            "handle_httpstatus_all": True,
            "request_scope": "page",
            "asset_mode": self.options.assets,
            "link_kind": record.get("link_kind"),
            "family_key": record.get("family_key"),
            "ordinal": record.get("ordinal"),
            "refresh_reason": reason,
            "previous_content_id": (
                record.get("content_id") if record.get("status") == "extracted" else None
            ),
            "previous_duplicate_of": record.get("duplicate_of"),
        }
        return scrapy.Request(
            url,
            callback=self.callback,
            errback=self.errback,
            priority=500,
            headers=conditional_headers(record),
            meta=meta,
            dont_filter=True,
        )
