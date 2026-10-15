from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import scrapy
from scrapy.http import Response

from data_pipeline.core.policies.access import classify_page_gate
from data_pipeline.core.policies.url import canonicalize_url
from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.extract.article import analyze_article
from data_pipeline.crawl.extract.fingerprint import change_fingerprint
from data_pipeline.crawl.frontier.seeds import SeedMode
from data_pipeline.crawl.frontier.sitemaps import SitemapParseError, parse_sitemap
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter, content_id_for
from data_pipeline.crawl.state.refresh_store import RefreshStore

if TYPE_CHECKING:
    from data_pipeline.crawl.spider.html import HtmlHandler


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


def response_validators(headers: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for header, key in ((b"ETag", "etag"), (b"Last-Modified", "last_modified")):
        raw = headers.get(header)
        if raw:
            values[key] = raw.decode("latin1") if isinstance(raw, bytes) else str(raw)
    return values


# Targets refetched as hubs: their own text changes with every new post and is not news.
_HUB_REASONS = frozenset({"homepage", "navigation", "pagination_head", "pagination_next"})
_GONE_STATUSES = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.GONE})


def classify_refetch(previous_text: str | None, text: str, gate_reason: str | None) -> str:
    """Classify a content page refetch by comparing change fingerprints of its two texts."""
    if gate_reason is not None:
        return f"gate_{gate_reason}"
    if previous_text is None:
        return "new"
    if change_fingerprint(previous_text) == change_fingerprint(text):
        return "unchanged"
    return "modified"


class RefreshSession:
    def __init__(self, store: RefreshStore, run_id: str, settings: RefreshSettings) -> None:
        self.store = store
        self.run_id = run_id
        self.settings = settings
        self._empty_pages: dict[str, int] = defaultdict(int)

    def follow_pagination(self, family_key: str, new_content_count: int) -> bool:
        if new_content_count > 0:
            self._empty_pages[family_key] = 0
            return True
        self._empty_pages[family_key] += 1
        return self._empty_pages[family_key] < self.settings.pagination_empty_pages

    def add_pagination_target(self, url: str) -> bool:
        return self.store.add_target(self.run_id, url, "pagination_next")

    def finish_target(self, url: str, outcome: str, error: str | None = None) -> None:
        self.store.finish_target(self.run_id, url, outcome, error)

    def record_change(
        self,
        url: str,
        change: str,
        *,
        page_role: str,
        content_id: str,
        previous_content_id: str | None = None,
    ) -> None:
        record: dict[str, object] = {
            "url": url,
            "change": change,
            "page_role": page_role,
            "content_id": content_id,
        }
        if previous_content_id is not None:
            record["previous_content_id"] = previous_content_id
        self.store.record_change(self.run_id, record)


class RefreshHandler:
    """Refetches known pages with conditional headers and classifies what changed."""

    def __init__(
        self,
        *,
        session: RefreshSession,
        html: HtmlHandler,
        writer: CrawlWriter,
        options: CrawlOptions,
        callback: Callable[..., Any],
        errback: Callable[..., Any],
    ) -> None:
        self.session = session
        self.html = html
        self.writer = writer
        self.options = options
        self.callback = callback
        self.errback = errback

    def request(self, url: str, reason: str) -> scrapy.Request:
        record = self.writer.state.url_record(url) or {}
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({hostname}),
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

    def pagination_request(self, url: str) -> scrapy.Request | None:
        canonical, _, _ = canonicalize_url(url)
        if not canonical:
            return None
        record = self.writer.state.url_record(canonical)
        if record is None or record.get("status") != "extracted":
            return None
        if not self.session.add_pagination_target(canonical):
            return None
        return self.request(canonical, "pagination_next")

    def handle(self, response: Response) -> Iterator[scrapy.Request]:
        url = str(response.meta.get("input_url", response.url))
        if response.status == HTTPStatus.NOT_MODIFIED:
            self.session.finish_target(url, "not_modified")
            return
        if response.status in _GONE_STATUSES and response.meta.get("previous_content_id"):
            self._removed(url, str(response.meta["previous_content_id"]))
            return
        if response.status >= HTTPStatus.MULTIPLE_CHOICES:
            self.session.finish_target(url, f"http_{response.status}")
            return
        if response.meta.get("response_purpose") in {"robots", "sitemap"}:
            if response.meta.get("response_purpose") == "sitemap":
                try:
                    parse_sitemap(
                        response.body,
                        response.url,
                        (response.headers.get(b"Content-Type") or b"").decode("latin1"),
                    )
                except SitemapParseError:
                    self.session.finish_target(url, "sitemap_parse_error")
                    return
            yield from self.html.handle(response)
            self.session.finish_target(url, "fetched")
            return
        content_type = (response.headers.get(b"Content-Type") or b"").decode(
            "latin1", errors="replace"
        )
        mime = content_type.partition(";")[0].strip().lower()
        if (
            "download_stopped" in getattr(response, "flags", [])
            or response.meta.get("header_stop_reason")
            or (mime and mime not in {"text/html", "application/xhtml+xml"})
        ):
            self.session.finish_target(url, "non_html")
            return
        analysis = analyze_article(response.body, response.url, getattr(response, "encoding", None))
        text = str(analysis.article.get("text") or "")
        gate = classify_page_gate(
            status=response.status, url=response.url, html=response.text, text=text
        )
        if not gate.expandable:
            self.session.finish_target(url, f"gate_{gate.reason}")
            return
        stored_article, outcome = self._store_refetch(response, analysis, url, content_type, text)
        self.html.store_validators(url, response, last_checked_run=self.session.run_id)
        self.session.finish_target(url, outcome)
        yield from self.html.expand(
            response,
            analysis,
            stored_article,
            url,
            cast(SeedMode, response.meta.get("seed_mode", "recursive")),
        )

    def _store_refetch(
        self, response: Response, analysis: Any, url: str, content_type: str, text: str
    ) -> tuple[dict[str, Any], str]:
        """Store a refetched page and return it with its outcome; record real changes only."""
        state = self.writer.state
        previous = response.meta.get("previous_content_id")
        if previous is not None and previous == content_id_for(text):
            unchanged: dict[str, Any] = {
                "url": url,
                "text": text,
                "content_id": previous,
                "duplicate_of": response.meta.get("previous_duplicate_of"),
            }
            return unchanged, "unchanged"
        previous_article = state.article(url) if previous is not None else None
        if previous is not None:
            state.release_content_owner(url, str(previous))
        article_data = self.html.article_record(response, analysis, url, content_type)
        stored = self.writer.write_article(article_data)
        is_hub = (
            response.meta.get("refresh_reason") in _HUB_REASONS
            or article_data["page_role"] == "navigation"
        )
        was_duplicate = bool(response.meta.get("previous_duplicate_of"))
        if is_hub or was_duplicate or stored.get("duplicate_of"):
            return stored, "fetched"
        previous_text = None if previous_article is None else str(previous_article.get("text"))
        outcome = classify_refetch(previous_text, text, None)
        if outcome != "unchanged":
            self.session.record_change(
                url,
                outcome,
                page_role=str(article_data["page_role"]),
                content_id=str(stored["content_id"]),
                previous_content_id=previous,
            )
        return stored, outcome

    def _removed(self, url: str, previous_content_id: str) -> None:
        """A once-extracted page answers 404/410: report it removed and drop it from final/."""
        state = self.writer.state
        previous = state.article(url) or {}
        state.release_content_owner(url, previous_content_id)
        state.mark_absent(url)
        self.session.record_change(
            url,
            "removed",
            page_role=str(previous.get("page_role") or "content"),
            content_id=previous_content_id,
        )
        self.session.finish_target(url, "removed")
