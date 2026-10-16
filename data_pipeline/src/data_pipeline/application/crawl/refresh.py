from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from data_pipeline.application.crawl.ports import CrawlStateStore, CrawlStorage, RefreshStorePort
from data_pipeline.application.crawl.refresh_pipeline import RefreshSession
from data_pipeline.application.crawl.requeue import restore_status
from data_pipeline.domain.crawl.extract.dates import parse_page_date
from data_pipeline.domain.crawl.refresh_rules import RefreshSettings
from data_pipeline.domain.crawl.run_status import CONTINUABLE_RUN_STATUSES, FINISHED_STATUSES

RefreshReason = Literal[
    "homepage", "sitemap", "navigation", "pagination_head", "recent", "pagination_next"
]
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


def new_run_id(now: float) -> str:
    return time.strftime(RUN_ID_FORMAT, time.gmtime(now))


def _run_timestamp(run_id: str) -> float:
    return datetime.strptime(run_id, RUN_ID_FORMAT).replace(tzinfo=UTC).timestamp()


def _timestamp(value: object) -> float | None:
    parsed = parse_page_date(str(value)) if value else None
    return datetime.fromisoformat(parsed).timestamp() if parsed else None


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def preflight(state_dir: Path, storage: CrawlStorage) -> str | None:
    """Return the refresh run to continue, or None to start a new one."""
    manifest = storage.manifest(state_dir)
    if manifest is None:
        msg = f"--refresh requires an existing crawl: {state_dir.parent}"
        raise ValueError(msg)
    latest = storage.latest_refresh_run(state_dir)
    if latest is not None and latest["status"] in CONTINUABLE_RUN_STATUSES:
        return str(latest["run_id"])
    status = manifest.get("status")
    if status not in FINISHED_STATUSES:
        msg = f"--refresh requires a finished crawl; current status is {status}"
        raise ValueError(msg)
    return None


def _homepages(state: CrawlStateStore, recursive_hostnames: Iterable[str]) -> list[str]:
    status = state.url_status
    urls: list[str] = []
    for hostname in sorted(recursive_hostnames):
        https_url, http_url = f"https://{hostname}/", f"http://{hostname}/"
        http_ok = status(http_url) == "extracted"
        # A host whose HTTPS homepage never worked was crawled over the HTTP
        # fallback; probing HTTPS again would fail on every refresh.
        if status(https_url) == "extracted" or not http_ok:
            urls.append(https_url)
        if http_ok:
            urls.append(http_url)
    return urls


def _sitemaps(state: CrawlStateStore) -> list[str]:
    rows = state.parsed_probes()
    sitemap_hosts = {_hostname(url) for url, purpose in rows if purpose == "sitemap"}
    return [
        str(url) for url, purpose in rows if purpose == "sitemap" or _hostname(url) in sitemap_hosts
    ]


def _pagination_heads(state: CrawlStateStore) -> list[str]:
    rows = state.pagination_members()
    heads: list[str] = []
    seen: set[str] = set()
    for family_key, url, parent in rows:
        if family_key in seen:
            continue
        seen.add(family_key)
        head = str(url)
        if parent and state.url_status(parent) == "extracted":
            head = parent
        heads.append(head)
    return heads


def select_recent(
    state: CrawlStateStore,
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[str]:
    cutoff = now - settings.recent_days * 86400
    runs = set(recent_run_ids)
    scored: list[tuple[float, str]] = []
    for url, published_at, modified_at, first_seen_run in state.recent_content_candidates():
        dated = [
            stamp
            for stamp in (_timestamp(modified_at), _timestamp(published_at))
            if stamp is not None
        ]
        if dated and max(dated) >= cutoff:
            scored.append((max(dated), str(url)))
        elif first_seen_run in runs:
            scored.append((_run_timestamp(str(first_seen_run)), str(url)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[: settings.recheck_limit]]


def select_targets(
    state: CrawlStateStore,
    recursive_hostnames: Iterable[str],
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[tuple[str, RefreshReason]]:
    groups: tuple[tuple[RefreshReason, list[str]], ...] = (
        ("homepage", _homepages(state, recursive_hostnames)),
        ("sitemap", _sitemaps(state)),
        ("navigation", state.navigation_pages()),
        ("pagination_head", _pagination_heads(state)),
        ("recent", select_recent(state, settings, recent_run_ids, now)),
    )
    selected: dict[str, RefreshReason] = {}
    for reason, urls in groups:
        for url in urls:
            selected.setdefault(url, reason)
    return sorted(selected.items())


def prepare_refresh_run(
    state: CrawlStateStore,
    store: RefreshStorePort,
    output: Path,
    recursive_hostnames: Iterable[str],
    settings: RefreshSettings,
    continue_run_id: str | None,
    *,
    now: float,
) -> tuple[str, list[tuple[str, str]]]:
    if continue_run_id is not None:
        store.resume_run(continue_run_id)
        state.refresh_run_id = continue_run_id
        return continue_run_id, store.pending_targets(continue_run_id)

    run_id = new_run_id(now)
    latest = store.latest_run()
    while latest is not None and run_id <= str(latest["run_id"]):
        now += 1
        run_id = new_run_id(now)
    try:
        state.backup_to(output / "archive" / f"pre-refresh-{run_id}" / "index.sqlite3")
        targets = select_targets(
            state,
            recursive_hostnames,
            settings,
            store.completed_run_ids(settings.recent_runs),
            now,
        )
        store.start_run(run_id)
        store.add_targets(run_id, targets)
    except Exception:
        restore_status(state)
        raise
    state.refresh_run_id = run_id
    return run_id, store.pending_targets(run_id)


def _export_changes(
    state: CrawlStateStore,
    changes: list[dict[str, object]],
    destination: Path,
    summary: dict[str, object],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    with (temporary / "changes.jsonl").open("w", encoding="utf-8") as file:
        for change in changes:
            file.write(json.dumps(change, ensure_ascii=False) + "\n")
    with (temporary / "articles.jsonl").open("w", encoding="utf-8") as file:
        for _, payload in state.iter_final_articles(str(change["url"]) for change in changes):
            file.write(f"{payload}\n")
    (temporary / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.rmtree(destination, ignore_errors=True)
    temporary.replace(destination)


def complete_refresh_run(
    state: CrawlStateStore, session: RefreshSession, output: Path, status: str
) -> dict[str, object]:
    store = session.store
    changes = list(store.iter_changes(session.run_id))
    summary: dict[str, object] = {
        "run_id": session.run_id,
        "crawl_id": state.crawl_id,
        "status": status,
        "started_at": store.run_started_at(session.run_id),
        "finished_at": time.time(),
        **store.target_summary(session.run_id),
        "new": sum(1 for change in changes if change["change"] == "new"),
        "modified": sum(1 for change in changes if change["change"] == "modified"),
        "removed": sum(1 for change in changes if change["change"] == "removed"),
    }
    store.finish_run(session.run_id, status, summary)
    if status not in {"interrupted", "failed"}:
        state.manifest["last_refresh_run_id"] = session.run_id
        _export_changes(state, changes, output / "changes" / session.run_id, summary)
    return summary
