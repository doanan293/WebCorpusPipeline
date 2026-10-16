from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from data_pipeline.application.crawl.ports import CrawlStateStore, CrawlStorage, RawHtmlStore
from data_pipeline.application.crawl.writer import content_id_for
from data_pipeline.domain.crawl.extract.article import analyze_article
from data_pipeline.domain.crawl.models import DUPLICATE_DROPPED_FIELDS
from data_pipeline.domain.crawl.run_status import FINISHED_STATUSES


@dataclass(frozen=True, slots=True)
class ReextractSummary:
    pages: int
    changed: int
    duplicates: int


def _charset(content_type: str) -> str | None:
    message = Message()
    message["Content-Type"] = content_type
    return message.get_content_charset()


def _extract_pages(state: CrawlStateStore, store: RawHtmlStore, urls: list[str]) -> dict[str, str]:
    """Rewrite every stored page's full article from its raw HTML; return url → content_id."""
    content_ids: dict[str, str] = {}
    for url in urls:
        raw = store.get(url)
        if raw is None:
            continue
        previous = state.article(url) or {}
        analysis = analyze_article(raw.body, raw.final_url, _charset(raw.content_type))
        text = str(analysis.article.get("text") or "")
        article = {
            "url": url,
            "final_url": raw.final_url,
            **analysis.article,
            "page_role": previous.get("page_role", "content"),
            "status": raw.status,
            "content_type": raw.content_type,
            "content_id": content_id_for(text),
        }
        content_ids[url] = str(article["content_id"])
        state.set_article_payload(url, article)
    return content_ids


def _owners(state: CrawlStateStore, content_ids: dict[str, str], empty_id: str) -> dict[str, str]:
    """Pick one owner per content id: the previous owner if it still has it, else the first URL."""
    previous = state.content_owners()
    owners: dict[str, str] = {}
    for url in sorted(content_ids):
        content_id = content_ids[url]
        if content_id == empty_id or content_id in owners:
            continue
        kept = previous.get(content_id)
        # An owner keeps its content unless re-extraction gave it a different content id.
        if kept is not None and content_ids.get(kept, content_id) == content_id:
            owners[content_id] = kept
        else:
            owners[content_id] = url
    return owners


def reextract(output: Path, storage: CrawlStorage) -> ReextractSummary:
    """Re-run extraction on every stored page of a finished crawl, offline, and rebuild final/."""
    state = storage.open_existing(output / "state")
    try:
        status = state.manifest.get("status")
        if status not in FINISHED_STATUSES:
            msg = f"reextract requires a finished crawl; current status is {status}"
            raise ValueError(msg)
        before = dict(state.reextract_candidates())
        with state.transaction():
            content_ids = _extract_pages(state, state.raw_html_store(output / "html"), [*before])
            owners = _owners(state, content_ids, content_id_for(""))
            duplicates = _apply_ownership(state, content_ids, owners)
        state.export_final(output)
        changed = sum(1 for url, content_id in content_ids.items() if before[url] != content_id)
        return ReextractSummary(pages=len(content_ids), changed=changed, duplicates=duplicates)
    finally:
        state.close()


def _apply_ownership(
    state: CrawlStateStore, content_ids: dict[str, str], owners: dict[str, str]
) -> int:
    """Store owners, turn every other page with the same content into a stub; count stubs."""
    state.replace_content_owners(content_ids, owners)
    duplicates = 0
    for url, content_id in content_ids.items():
        owner = owners.get(content_id, url)
        if owner != url:
            duplicates += 1
            article = state.article(url) or {}
            for field in ("text", *DUPLICATE_DROPPED_FIELDS):
                article.pop(field, None)
            article["duplicate_of"] = owner
            state.set_article_payload(url, article)
        state.merge_url(
            {
                "url": url,
                "status": "extracted",
                "content_id": content_id,
                "duplicate_of": owner if owner != url else None,
            },
            completed=True,
        )
    return duplicates
