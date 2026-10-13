from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.state.refresh_store import CONTINUABLE_RUN_STATUSES, RefreshStore
from data_pipeline.crawl.state.store import CrawlState

RefreshReason = Literal[
    "homepage", "sitemap", "navigation", "pagination_head", "recent", "pagination_next"
]
FINISHED_CRAWL_STATUSES = frozenset({"complete", "complete_with_failures", "truncated"})
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


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


def new_run_id(now: float) -> str:
    return time.strftime(RUN_ID_FORMAT, time.gmtime(now))


def _run_timestamp(run_id: str) -> float:
    return datetime.strptime(run_id, RUN_ID_FORMAT).replace(tzinfo=UTC).timestamp()


def _timestamp(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _latest_run(db_path: Path) -> tuple[str, str] | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'refresh_runs';"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT run_id, status FROM refresh_runs ORDER BY run_id DESC LIMIT 1;"
        ).fetchone()
        return (str(row[0]), str(row[1])) if row else None
    finally:
        connection.close()


def preflight(state_dir: Path) -> str | None:
    """Return the refresh run to continue, or None to start a new one."""
    manifest_path = state_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"--refresh requires an existing crawl: {state_dir.parent}")
    latest = _latest_run(state_dir / "index.sqlite3")
    if latest is not None and latest[1] in CONTINUABLE_RUN_STATUSES:
        return latest[0]
    status = json.loads(manifest_path.read_text(encoding="utf-8")).get("status")
    if status not in FINISHED_CRAWL_STATUSES:
        raise ValueError(f"--refresh requires a finished crawl; current status is {status}")
    return None


def _homepages(connection: sqlite3.Connection, recursive_hostnames: Iterable[str]) -> list[str]:
    def status(url: str) -> str | None:
        row = connection.execute("SELECT status FROM url_records WHERE url = ?;", (url,)).fetchone()
        return str(row[0]) if row else None

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


def _sitemaps(connection: sqlite3.Connection) -> list[str]:
    # A robots/sitemap response that parsed successfully keeps status 'scheduled'
    # and is only checkpointed; absent or failed ones get another status.
    rows = connection.execute(
        """
        SELECT url, json_extract(payload, '$.response_purpose')
        FROM url_records
        WHERE completed = 1 AND status = 'scheduled'
          AND json_extract(payload, '$.response_purpose') IN ('robots', 'sitemap')
        ORDER BY url;
        """
    ).fetchall()
    sitemap_hosts = {_hostname(url) for url, purpose in rows if purpose == "sitemap"}
    return [
        str(url) for url, purpose in rows if purpose == "sitemap" or _hostname(url) in sitemap_hosts
    ]


def _navigation_pages(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
        WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'navigation'
        ORDER BY a.url;
        """
    )
    return [str(row[0]) for row in rows]


def _pagination_heads(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT json_extract(payload, '$.family_key'), url, json_extract(payload, '$.discovered_from')
        FROM url_records
        WHERE status = 'extracted' AND json_extract(payload, '$.family_key') IS NOT NULL
        ORDER BY json_extract(payload, '$.family_key'),
                 json_extract(payload, '$.ordinal') IS NULL,
                 json_extract(payload, '$.ordinal'),
                 url;
        """
    ).fetchall()
    heads: list[str] = []
    seen: set[str] = set()
    for family_key, url, parent in rows:
        if family_key in seen:
            continue
        seen.add(family_key)
        head = str(url)
        if parent:
            parent_row = connection.execute(
                "SELECT status FROM url_records WHERE url = ?;", (parent,)
            ).fetchone()
            if parent_row is not None and parent_row[0] == "extracted":
                head = str(parent)
        heads.append(head)
    return heads


def select_recent(
    connection: sqlite3.Connection,
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[str]:
    cutoff = now - settings.recent_days * 86400
    runs = set(recent_run_ids)
    scored: list[tuple[float, str]] = []
    rows = connection.execute(
        """
        SELECT a.url,
               json_extract(a.payload, '$.published_at'),
               json_extract(a.payload, '$.modified_at'),
               json_extract(r.payload, '$.first_seen_run')
        FROM articles AS a JOIN url_records AS r ON r.url = a.url
        WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'content';
        """
    )
    for url, published_at, modified_at, first_seen_run in rows:
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
    connection: sqlite3.Connection,
    recursive_hostnames: Iterable[str],
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[tuple[str, RefreshReason]]:
    groups: tuple[tuple[RefreshReason, list[str]], ...] = (
        ("homepage", _homepages(connection, recursive_hostnames)),
        ("sitemap", _sitemaps(connection)),
        ("navigation", _navigation_pages(connection)),
        ("pagination_head", _pagination_heads(connection)),
        ("recent", select_recent(connection, settings, recent_run_ids, now)),
    )
    selected: dict[str, RefreshReason] = {}
    for reason, urls in groups:
        for url in urls:
            selected.setdefault(url, reason)
    return sorted(selected.items())


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


def classify_refetch(
    previous_content_id: str | None, content_id: str, gate_reason: str | None
) -> str:
    if gate_reason is not None:
        return f"gate_{gate_reason}"
    if previous_content_id is None:
        return "new"
    return "unchanged" if previous_content_id == content_id else "modified"


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


def prepare_refresh_run(
    state: CrawlState,
    store: RefreshStore,
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
            state.connection,
            recursive_hostnames,
            settings,
            store.completed_run_ids(settings.recent_runs),
            now,
        )
        store.start_run(run_id)
        store.add_targets(run_id, targets)
    except Exception:
        state.restore_status()
        raise
    state.refresh_run_id = run_id
    return run_id, store.pending_targets(run_id)


def _export_changes(
    state: CrawlState,
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
    os.replace(temporary, destination)


def complete_refresh_run(
    state: CrawlState, session: RefreshSession, output: Path, status: str
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
    }
    store.finish_run(session.run_id, status, summary)
    if status not in {"interrupted", "failed"}:
        state.manifest["last_refresh_run_id"] = session.run_id
        _export_changes(state, changes, output / "changes" / session.run_id, summary)
    return summary
