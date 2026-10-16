"""In-memory stand-ins for the crawl state and writer."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from data_pipeline.application.crawl.ports import EngineResult, SpiderSetup
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.frontier.assets import AssetRole
from data_pipeline.domain.crawl.models import RawPage, RouteFamilyState
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.tuning import RuntimeTuning


class FakeCrawlState:
    """A crawl state in memory: records, articles, files and errors as plain lists and dicts."""

    def __init__(self, root: Path = Path("state")) -> None:
        self.root = root
        self.manifest: dict[str, Any] = {"input_sha256": "fake", "started_at": 0}
        self.refresh_run_id: str | None = None
        self.completed: set[str] = set()
        self.articles: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.discovered: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.targets: list[dict[str, Any]] = []
        self.url_records: dict[str, dict[str, Any]] = {}
        self.owners: dict[str, str] = {}
        self._asset_referrers: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.families: dict[str, RouteFamilyState] = {}
        self.hosts: dict[str, dict[str, object]] = {}
        self.refresh = FakeRefreshStore()
        self.finished: dict[str, object] | None = None

    @property
    def crawl_id(self) -> str:
        return "fake-crawl"

    def write_manifest(self, value: dict[str, Any]) -> None:
        self.manifest = value

    def is_complete(self, url: str) -> bool:
        return url in self.completed

    def is_finished(self, url: str) -> bool:
        record = self.url_records.get(url, {})
        return url in self.completed and (
            record.get("status") in {"extracted", "file_saved", "failed", "absent"}
            or (record.get("status") == "skipped" and record.get("frontier_action") == "scheduled")
        )

    def checkpoint(self, url: str) -> None:
        self.completed.add(url)

    def merge_url(
        self,
        record: Mapping[str, object],
        *,
        completed: bool = False,
        keep_scheduled: bool = False,
    ) -> dict[str, object]:
        url = str(record["url"])
        existing = self.url_records.get(url, {})
        merged = dict(existing)
        merged.update({k: v for k, v in record.items() if v is not None})
        self.url_records[url] = merged
        if completed or merged.get("completed"):
            self.completed.add(url)
        return merged

    def complete_url(
        self,
        disposition: Mapping[str, object],
        *,
        article: Mapping[str, object] | None = None,
        file: Mapping[str, object] | None = None,
        error: Mapping[str, object] | None = None,
    ) -> None:
        if article is not None:
            self.articles.append(dict(article))
        if file is not None:
            self.files.append(dict(file))
        if error is not None:
            self.errors.append(dict(error))
        self.merge_url(disposition, completed=True)

    def upsert_url(self, record: Mapping[str, object], *, completed: bool = False) -> None:
        self.merge_url(record, completed=completed)

    def put_error(self, record: Mapping[str, object]) -> None:
        self.errors.append(dict(record))

    def insert_url_if_absent(self, record: Mapping[str, object]) -> None:
        if str(record["url"]) not in self.url_records:
            self.merge_url(record, completed=True)

    def claim_content(self, content_id: str, url: str) -> str:
        return self.owners.setdefault(content_id, url)

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None:
        self._asset_referrers[url].add((page_url, role))

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        return tuple(sorted({page for page, _ in self._asset_referrers.get(url, ())}))

    def url_record(self, url: str) -> dict[str, object] | None:
        return self.url_records.get(url)

    def url_status(self, url: str) -> str | None:
        record = self.url_records.get(url)
        return None if record is None else str(record.get("status"))

    def article(self, url: str) -> dict[str, object] | None:
        return next((a for a in reversed(self.articles) if a.get("url") == url), None)

    def file_record(self, url: str) -> dict[str, object] | None:
        return next((f for f in reversed(self.files) if f.get("url") == url), None)

    def release_content_owner(self, url: str, content_id: str) -> None:
        if self.owners.get(content_id) == url:
            del self.owners[content_id]

    def mark_absent(self, url: str) -> None:
        self.url_records.setdefault(url, {"url": url})["status"] = "absent"

    def update_url_fields(self, url: str, fields: Mapping[str, object]) -> None:
        if url in self.url_records:
            self.url_records[url].update(fields)

    def backup_to(self, destination: Path) -> None:
        return None

    def counts(self) -> dict[str, int]:
        return dict(Counter(f"url_status_{r.get('status')}" for r in self.url_records.values()))

    def put_target(self, record: Mapping[str, object]) -> None:
        self.targets.append(dict(record))

    def iter_url_records(self) -> Iterator[dict[str, object]]:
        return iter([self.url_records[url] for url in sorted(self.url_records)])

    def iter_pending_scheduled_records(self) -> Iterator[dict[str, object]]:
        return iter(
            [
                record
                for url, record in sorted(self.url_records.items())
                if url not in self.completed
                and record.get("status") == "scheduled"
                and record.get("frontier_action") == "scheduled"
            ]
        )

    def parsed_probes(self) -> list[tuple[str, str]]:
        return []

    def navigation_pages(self) -> list[str]:
        return []

    def pagination_members(self) -> list[tuple[str, str, str | None]]:
        return []

    def recent_content_candidates(self) -> list[tuple[str, object, object, object]]:
        return []

    def failed_records(self) -> list[tuple[str, dict[str, object], dict[str, object]]]:
        return []

    def skipped_records(self) -> list[tuple[str, dict[str, object]]]:
        return []

    def requeue(self, records: list[tuple[str, dict[str, object]]]) -> int:
        for url, record in records:
            self.url_records[url] = record
            self.completed.discard(url)
        return len(records)

    def reextract_candidates(self) -> list[tuple[str, object]]:
        return []

    def set_article_payload(self, url: str, article: Mapping[str, object]) -> None:
        self.articles.append(dict(article))

    def content_owners(self) -> dict[str, str]:
        return dict(self.owners)

    def replace_content_owners(self, urls: Iterable[str], owners: Mapping[str, str]) -> None:
        gone = set(urls)
        self.owners = {k: v for k, v in self.owners.items() if v not in gone}
        self.owners.update(owners)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def raw_html_store(self, directory: Path) -> FakeHtmlStore:
        return FakeHtmlStore()

    def refresh_store(self) -> FakeRefreshStore:
        return self.refresh

    def export_crawl(self, output: Path) -> None:
        return None

    def export_discovery(self, output: Path) -> None:
        return None

    def export_final(self, output: Path) -> None:
        return None

    def iter_final_articles(self, urls: Iterable[str] | None = None) -> Iterator[tuple[str, str]]:
        return iter(())

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]:
        return {}

    def route_families(self) -> Iterator[RouteFamilyState]:
        return iter(self.families.values())

    def put_route_family(self, family: RouteFamilyState) -> None:
        self.families[family.family_key] = family

    def host_discovery(self) -> Iterator[dict[str, object]]:
        return iter(self.hosts.values())

    def upsert_host_discovery(self, record: Mapping[str, object]) -> None:
        self.hosts[str(record["hostname"])] = dict(record)

    def host_completion(self, hostnames: frozenset[str]) -> dict[str, dict[str, object]]:
        return {}

    def finish(
        self,
        status: str,
        exit_code: int,
        counts: dict[str, int],
        metrics: Mapping[str, object],
        truncation: Mapping[str, object] | None = None,
        *,
        discovery: Mapping[str, object] | None = None,
        frontier: Mapping[str, object] | None = None,
        host_completion: Mapping[str, Mapping[str, object]] | None = None,
        public_output: Path | None = None,
    ) -> None:
        self.finished = {"status": status, "exit_code": exit_code}

    def close(self) -> None:
        return None


class FakeHtmlStore:
    """Raw pages in memory, keyed by URL."""

    def __init__(self) -> None:
        self.pages: dict[str, RawPage] = {}

    def put(
        self,
        url: str,
        body: bytes,
        *,
        final_url: str,
        status: int,
        content_type: str,
        fetched_at: str,
    ) -> str:
        self.pages[url] = RawPage(url, final_url, status, content_type, fetched_at, body)
        return hashlib.sha256(body).hexdigest()

    def get(self, url: str) -> RawPage | None:
        return self.pages.get(url)


class FakePreparedCrawl:
    def __init__(self, result: EngineResult) -> None:
        self.result = result

    def start(self) -> EngineResult:
        return self.result


class FakeEngine:
    """Records each spider setup it is given and reports the stats it was built with."""

    def __init__(self, stats: dict[str, Any] | None = None) -> None:
        self.setups: list[SpiderSetup] = []
        self.stats = stats or {"finish_reason": "finished"}

    def prepare(
        self, config: CrawlerConfig, tuning: RuntimeTuning, setup: SpiderSetup
    ) -> FakePreparedCrawl:
        self.setups.append(setup)
        return FakePreparedCrawl(EngineResult(dict(self.stats), {}))


class FakeCrawlWriter:
    def __init__(self, state: FakeCrawlState, options: CrawlOptions) -> None:
        self.state = state
        self.options = options
        self.articles: list[dict[str, Any]] = state.articles
        self.files: list[dict[str, Any]] = state.files
        self.errors: list[dict[str, Any]] = state.errors
        self.discovered: list[dict[str, Any]] = state.discovered
        self.skipped: list[dict[str, Any]] = state.skipped
        self.url_records = state.url_records
        self.checkpoints: list[str] = []
        self.next_duplicate_of: str | None = None

    @property
    def discovered_assets(self) -> list[dict[str, Any]]:
        return [r for r in self.discovered if r.get("status") == "discovered_not_downloaded"]

    def record_scheduled(self, record: dict[str, Any]) -> None:
        self.state.merge_url(record, completed=False)

    def record_discovered(self, record: dict[str, Any], *, completed: bool = True) -> None:
        self.discovered.append(record)
        self.state.merge_url(record, completed=completed)

    def record_skipped(self, record: dict[str, Any]) -> None:
        self.skipped.append(record)
        self.state.merge_url(record, completed=True)

    def store_html(self, url: str, body: bytes, **metadata: object) -> None:
        return None

    def record_rejected(self, record: dict[str, Any]) -> None:
        self.record_skipped(record)

    def record_navigation_skip(self, record: dict[str, Any]) -> None:
        if record["url"] not in self.state.url_records:
            self.record_skipped(record)

    def record_absent(self, record: dict[str, Any]) -> None:
        self.discovered.append(record)
        self.state.complete_url(record)

    def write_article(self, record: dict[str, Any]) -> dict[str, Any]:
        text = str(record.get("text") or "").strip()
        content_id = record.get("content_id") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec = dict(record)
        rec["content_id"] = content_id
        if self.next_duplicate_of is not None:
            rec["duplicate_of"] = self.next_duplicate_of
            self.next_duplicate_of = None
        self.state.complete_url({"url": record["url"], "status": "extracted"}, article=rec)
        return rec

    def write_file(
        self,
        url: str,
        final_url: str,
        content_type: str,
        body: bytes,
        *,
        asset_group: str = "document",
        asset_role: str | None = None,
        referring_page: str | None = None,
    ) -> dict[str, Any]:
        rec = {
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "size": len(body),
            "asset_group": asset_group,
            "asset_role": asset_role,
            "referring_page": referring_page,
        }
        self.state.complete_url(
            {"url": url, "final_url": final_url, "status": "file_saved", "asset_role": asset_role},
            file=rec,
        )
        return rec

    def write_error(self, record: dict[str, Any]) -> None:
        self.state.complete_url(
            {
                "url": record["url"],
                "status": "failed",
                "reason": record.get("reason", record.get("error", "error")),
            },
            error=record,
        )

    def checkpoint(self, url: str) -> None:
        self.checkpoints.append(url)
        self.state.checkpoint(url)


class FakeRefreshStore:
    """Refresh runs in memory: targets per run, the outcome of each, and recorded changes."""

    def __init__(self) -> None:
        self.targets: dict[str, dict[str, str]] = {}
        self.outcomes: dict[str, str] = {}
        self.changes: list[dict[str, object]] = []
        self.runs: dict[str, dict[str, object]] = {}

    def start_run(self, run_id: str) -> None:
        self.runs[run_id] = {"run_id": run_id, "status": "running", "started_at": 0.0}

    def resume_run(self, run_id: str) -> None:
        self.runs[run_id]["status"] = "running"

    def latest_run(self) -> dict[str, object] | None:
        return self.runs[max(self.runs)] if self.runs else None

    def run_started_at(self, run_id: str) -> float:
        return float(str(self.runs[run_id]["started_at"]))

    def finish_run(self, run_id: str, status: str, payload: dict[str, object]) -> None:
        self.runs[run_id].update(payload, status=status)

    def completed_run_ids(self, limit: int) -> list[str]:
        done = [run for run, value in self.runs.items() if value["status"] != "running"]
        return sorted(done, reverse=True)[:limit]

    def add_targets(self, run_id: str, targets: Iterable[tuple[str, str]]) -> None:
        for url, reason in targets:
            self.add_target(run_id, url, reason)

    def add_target(self, run_id: str, url: str, reason: str) -> bool:
        run = self.targets.setdefault(run_id, {})
        if url in run:
            return False
        run[url] = reason
        return True

    def pending_targets(self, run_id: str) -> list[tuple[str, str]]:
        run = self.targets.get(run_id, {})
        return sorted((url, reason) for url, reason in run.items() if url not in self.outcomes)

    def finish_target(self, run_id: str, url: str, outcome: str, error: str | None = None) -> None:
        self.outcomes[url] = outcome

    def record_change(self, run_id: str, change: dict[str, object]) -> None:
        self.changes.append(change)

    def iter_changes(self, run_id: str) -> Iterator[dict[str, object]]:
        return iter(list(self.changes))

    def target_summary(self, run_id: str) -> dict[str, dict[str, int]]:
        return {"outcomes": dict(Counter(self.outcomes.values()))}

    def failure_counts(self, run_id: str, truncation_reasons: Iterable[str]) -> tuple[int, int]:
        return 0, 0


class FakeDiscoveryWriter:
    """Discovery results in memory: targets, probe records, errors and checkpoints."""

    def __init__(self, state: FakeCrawlState) -> None:
        self.state = state
        self.targets: list[dict[str, Any]] = []
        self.probes: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.checkpoints: list[str] = []

    def record_target(self, record: dict[str, Any]) -> None:
        self.targets.append(record)

    def record_probe(self, record: dict[str, Any]) -> None:
        self.probes.append(record)

    def write_error(self, record: dict[str, Any]) -> None:
        self.errors.append(record)

    def checkpoint(self, url: str) -> None:
        self.checkpoints.append(url)
        self.state.checkpoint(url)
