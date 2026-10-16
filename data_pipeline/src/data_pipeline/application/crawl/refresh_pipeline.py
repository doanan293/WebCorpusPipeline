from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

from data_pipeline.application.crawl.ports import RefreshRuns
from data_pipeline.application.crawl.writer import content_id_for
from data_pipeline.domain.crawl.access import classify_page_gate
from data_pipeline.domain.crawl.extract.article import analyze_article
from data_pipeline.domain.crawl.frontier.sitemaps import SitemapParseError, parse_sitemap
from data_pipeline.domain.crawl.pages import FetchedPage, FollowUp
from data_pipeline.domain.crawl.refresh_rules import (
    GONE_STATUSES,
    HUB_REASONS,
    RefreshSettings,
    classify_refetch,
)
from data_pipeline.domain.crawl.seeds import SeedMode

if TYPE_CHECKING:
    from data_pipeline.application.crawl.page_pipeline import PagePipeline


class RefreshSession:
    def __init__(self, store: RefreshRuns, run_id: str, settings: RefreshSettings) -> None:
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


class RefreshPipeline:
    """Classifies what changed on a page refetched with conditional headers."""

    def __init__(self, session: RefreshSession, pages: PagePipeline) -> None:
        self.session = session
        self.pages = pages

    def handle(self, page: FetchedPage) -> Iterator[FollowUp]:
        url = page.input_url
        meta = page.meta
        if page.status == HTTPStatus.NOT_MODIFIED:
            self.session.finish_target(url, "not_modified")
            return
        if page.status in GONE_STATUSES and meta.get("previous_content_id"):
            self._removed(url, str(meta["previous_content_id"]))
            return
        if page.status >= HTTPStatus.MULTIPLE_CHOICES:
            self.session.finish_target(url, f"http_{page.status}")
            return
        if meta.get("response_purpose") in {"robots", "sitemap"}:
            if meta.get("response_purpose") == "sitemap":
                try:
                    parse_sitemap(page.body, page.url, page.content_type)
                except SitemapParseError:
                    self.session.finish_target(url, "sitemap_parse_error")
                    return
            yield from self.pages.process(page)
            self.session.finish_target(url, "fetched")
            return
        content_type = page.content_type
        mime = content_type.partition(";")[0].strip().lower()
        if (
            "download_stopped" in page.flags
            or meta.get("header_stop_reason")
            or (mime and mime not in {"text/html", "application/xhtml+xml"})
        ):
            self.session.finish_target(url, "non_html")
            return
        analysis = analyze_article(page.body, page.url, page.encoding)
        text = str(analysis.article.get("text") or "")
        gate = classify_page_gate(status=page.status, url=page.url, html=page.text(), text=text)
        if not gate.expandable:
            self.session.finish_target(url, f"gate_{gate.reason}")
            return
        stored_article, outcome = self._store_refetch(page, analysis, url, content_type, text)
        self.pages.writer.store_html(
            url,
            page.body,
            final_url=page.url,
            status=page.status,
            content_type=content_type,
        )
        self.pages.store_validators(url, page, last_checked_run=self.session.run_id)
        self.session.finish_target(url, outcome)
        yield from self.pages.expand(
            page,
            analysis,
            stored_article,
            url,
            cast(SeedMode, meta.get("seed_mode", "recursive")),
        )

    def _store_refetch(
        self, page: FetchedPage, analysis: Any, url: str, content_type: str, text: str
    ) -> tuple[dict[str, Any], str]:
        """Store a refetched page and return it with its outcome; record real changes only."""
        writer = self.pages.writer
        previous = page.meta.get("previous_content_id")
        if previous is not None and previous == content_id_for(text):
            unchanged: dict[str, Any] = {
                "url": url,
                "text": text,
                "content_id": previous,
                "duplicate_of": page.meta.get("previous_duplicate_of"),
            }
            return unchanged, "unchanged"
        previous_article = writer.articles.article(url) if previous is not None else None
        if previous is not None:
            writer.owners.release_content_owner(url, str(previous))
        article_data = self.pages.article_record(page, analysis, url, content_type)
        stored = writer.write_article(article_data)
        is_hub = (
            page.meta.get("refresh_reason") in HUB_REASONS
            or article_data["page_role"] == "navigation"
        )
        was_duplicate = bool(page.meta.get("previous_duplicate_of"))
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
        writer = self.pages.writer
        previous = writer.articles.article(url) or {}
        writer.owners.release_content_owner(url, previous_content_id)
        writer.urls.mark_absent(url)
        self.session.record_change(
            url,
            "removed",
            page_role=str(previous.get("page_role") or "content"),
            content_id=previous_content_id,
        )
        self.session.finish_target(url, "removed")
