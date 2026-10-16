from __future__ import annotations

import json
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from data_pipeline.crawl.application.writer import content_id_for
from data_pipeline.crawl.domain.extract.article import analyze_article
from data_pipeline.crawl.infrastructure.sqlite.export import DUPLICATE_DROPPED_FIELDS, export_final
from data_pipeline.crawl.infrastructure.sqlite.html_store import HtmlStore
from data_pipeline.crawl.infrastructure.sqlite.refresh_store import FINISHED_STATUSES
from data_pipeline.crawl.infrastructure.sqlite.state_store import CrawlState


@dataclass(frozen=True, slots=True)
class ReextractSummary:
    pages: int
    changed: int
    duplicates: int


def _charset(content_type: str) -> str | None:
    message = Message()
    message["Content-Type"] = content_type
    return message.get_content_charset()


def _extract_pages(state: CrawlState, store: HtmlStore, urls: list[str]) -> dict[str, str]:
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
        state.connection.execute(
            "UPDATE articles SET payload = ? WHERE url = ?;",
            (json.dumps(article, ensure_ascii=False), url),
        )
    return content_ids


def _owners(state: CrawlState, content_ids: dict[str, str], empty_id: str) -> dict[str, str]:
    """Pick one owner per content id: the previous owner if it still has it, else the first URL."""
    rows = state.connection.execute("SELECT content_id, url FROM content_owner;")
    previous = {str(content_id): str(url) for content_id, url in rows}
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


def reextract(output: Path) -> ReextractSummary:
    """Re-run extraction on every stored page of a finished crawl, offline, and rebuild final/."""
    state = CrawlState.open_existing(output / "state")
    try:
        status = state.manifest.get("status")
        if status not in FINISHED_STATUSES:
            msg = f"reextract requires a finished crawl; current status is {status}"
            raise ValueError(msg)
        rows = state.connection.execute(
            """
            SELECT r.url, json_extract(r.payload, '$.content_id')
            FROM url_records AS r JOIN raw_pages AS p ON p.url = r.url
            WHERE r.status = 'extracted' ORDER BY r.url;
            """
        ).fetchall()
        before = {str(url): content_id for url, content_id in rows}
        state.connection.execute("BEGIN IMMEDIATE;")
        try:
            content_ids = _extract_pages(
                state, HtmlStore(output / "html", state.connection), [*before]
            )
            owners = _owners(state, content_ids, content_id_for(""))
            duplicates = _apply_ownership(state, content_ids, owners)
            state.connection.execute("COMMIT;")
        except Exception:
            state.connection.execute("ROLLBACK;")
            raise
        export_final(state, output)
        changed = sum(1 for url, content_id in content_ids.items() if before[url] != content_id)
        return ReextractSummary(pages=len(content_ids), changed=changed, duplicates=duplicates)
    finally:
        state.close()


def _apply_ownership(state: CrawlState, content_ids: dict[str, str], owners: dict[str, str]) -> int:
    """Store owners, turn every other page with the same content into a stub; count stubs."""
    state.connection.executemany(
        "DELETE FROM content_owner WHERE url = ?;", ((url,) for url in content_ids)
    )
    state.connection.executemany(
        "INSERT OR REPLACE INTO content_owner (content_id, url) VALUES (?, ?);",
        owners.items(),
    )
    duplicates = 0
    for url, content_id in content_ids.items():
        owner = owners.get(content_id, url)
        if owner != url:
            duplicates += 1
            article = state.article(url) or {}
            for field in ("text", *DUPLICATE_DROPPED_FIELDS):
                article.pop(field, None)
            article["duplicate_of"] = owner
            state.connection.execute(
                "UPDATE articles SET payload = ? WHERE url = ?;",
                (json.dumps(article, ensure_ascii=False), url),
            )
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
