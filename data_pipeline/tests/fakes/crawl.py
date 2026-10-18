"""In-memory stand-ins for the crawl state and writer."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_pipeline.application.crawl.ports import EngineResult, SpiderSetup
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.models import RawPage, RouteFamilyState
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.tuning import RuntimeTuning
from data_pipeline.domain.shared.assets import AssetRole


@dataclass
class _Memory:
    """What one fake crawl state holds; every fake repository of a unit of work shares it."""

    completed: set[str] = field(default_factory=set)
    articles: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    discovered: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    url_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    asset_referrers: dict[str, set[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(set)
    )
    families: dict[str, RouteFamilyState] = field(default_factory=dict)
    hosts: dict[str, dict[str, object]] = field(default_factory=dict)


class FakeUrlRecords:
    """URL records in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def merge_url(
        self,
        record: Mapping[str, object],
        *,
        completed: bool = False,
        keep_scheduled: bool = False,
    ) -> dict[str, object]:
        url = str(record["url"])
        existing = self.memory.url_records.get(url, {})
        merged = dict(existing)
        merged.update({k: v for k, v in record.items() if v is not None})
        self.memory.url_records[url] = merged
        if completed or merged.get("completed"):
            self.memory.completed.add(url)
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
            self.memory.articles.append(dict(article))
        if file is not None:
            self.memory.files.append(dict(file))
        if error is not None:
            self.memory.errors.append(dict(error))
        self.merge_url(disposition, completed=True)

    def upsert_url(self, record: Mapping[str, object], *, completed: bool = False) -> None:
        self.merge_url(record, completed=completed)

    def insert_url_if_absent(self, record: Mapping[str, object]) -> None:
        if str(record["url"]) not in self.memory.url_records:
            self.merge_url(record, completed=True)

    def put_error(self, record: Mapping[str, object]) -> None:
        self.memory.errors.append(dict(record))

    def is_finished(self, url: str) -> bool:
        record = self.memory.url_records.get(url, {})
        return url in self.memory.completed and (
            record.get("status") in {"extracted", "file_saved", "failed", "absent"}
            or (record.get("status") == "skipped" and record.get("frontier_action") == "scheduled")
        )

    def is_complete(self, url: str) -> bool:
        return url in self.memory.completed

    def checkpoint(self, url: str) -> None:
        self.memory.completed.add(url)

    def url_record(self, url: str) -> dict[str, object] | None:
        return self.memory.url_records.get(url)

    def url_status(self, url: str) -> str | None:
        record = self.memory.url_records.get(url)
        return None if record is None else str(record.get("status"))

    def update_url_fields(self, url: str, fields: Mapping[str, object]) -> None:
        if url in self.memory.url_records:
            self.memory.url_records[url].update(fields)

    def mark_absent(self, url: str) -> None:
        self.memory.url_records.setdefault(url, {"url": url})["status"] = "absent"

    def iter_url_records(self) -> Iterator[dict[str, object]]:
        return iter([self.memory.url_records[url] for url in sorted(self.memory.url_records)])

    def iter_pending_scheduled_records(self) -> Iterator[dict[str, object]]:
        return iter(
            [
                record
                for url, record in sorted(self.memory.url_records.items())
                if url not in self.memory.completed
                and record.get("status") == "scheduled"
                and record.get("frontier_action") == "scheduled"
            ]
        )

    def parsed_probes(self) -> list[tuple[str, str]]:
        return []

    def pagination_members(self) -> list[tuple[str, str, str | None]]:
        return []

    def failed_records(self) -> list[tuple[str, dict[str, object], dict[str, object]]]:
        return []

    def skipped_records(self) -> list[tuple[str, dict[str, object]]]:
        return []

    def requeue(self, records: list[tuple[str, dict[str, object]]]) -> int:
        for url, record in records:
            self.memory.url_records[url] = record
            self.memory.completed.discard(url)
        return len(records)


class FakeArticles:
    """Articles in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def article(self, url: str) -> dict[str, object] | None:
        return next((a for a in reversed(self.memory.articles) if a.get("url") == url), None)

    def set_article_payload(self, url: str, article: Mapping[str, object]) -> None:
        self.memory.articles.append(dict(article))

    def reextract_candidates(self) -> list[tuple[str, object]]:
        return []

    def navigation_pages(self) -> list[str]:
        return []

    def recent_content_candidates(self) -> list[tuple[str, object, object, object]]:
        return []

    def iter_final_articles(self, urls: Iterable[str] | None = None) -> Iterator[tuple[str, str]]:
        return iter(())


class FakeContentOwners:
    """Content owners in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def claim_content(self, content_id: str, url: str) -> str:
        return self.memory.owners.setdefault(content_id, url)

    def release_content_owner(self, url: str, content_id: str) -> None:
        if self.memory.owners.get(content_id) == url:
            del self.memory.owners[content_id]

    def content_owners(self) -> dict[str, str]:
        return dict(self.memory.owners)

    def replace_content_owners(self, urls: Iterable[str], owners: Mapping[str, str]) -> None:
        gone = set(urls)
        self.memory.owners = {k: v for k, v in self.memory.owners.items() if v not in gone}
        self.memory.owners.update(owners)


class FakeAssets:
    """Saved files and their referrers in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def file_record(self, url: str) -> dict[str, object] | None:
        return next((f for f in reversed(self.memory.files) if f.get("url") == url), None)

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None:
        self.memory.asset_referrers[url].add((page_url, role))

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        return tuple(sorted({page for page, _ in self.memory.asset_referrers.get(url, ())}))


class FakeRouteFamilies:
    """Route families in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def route_families(self) -> Iterator[RouteFamilyState]:
        return iter(self.memory.families.values())

    def put_route_family(self, family: RouteFamilyState) -> None:
        self.memory.families[family.family_key] = family


class FakeHostDiscovery:
    """Host discovery and targets in the shared memory."""

    def __init__(self, memory: _Memory) -> None:
        self.memory = memory

    def host_discovery(self) -> Iterator[dict[str, object]]:
        return iter(self.memory.hosts.values())

    def upsert_host_discovery(self, record: Mapping[str, object]) -> None:
        self.memory.hosts[str(record["hostname"])] = dict(record)

    def host_completion(self, hostnames: frozenset[str]) -> dict[str, dict[str, object]]:
        return {}

    def put_target(self, record: Mapping[str, object]) -> None:
        self.memory.targets.append(dict(record))


class FakeCrawlRun:
    """The run's manifest, counts and finish record."""

    def __init__(self, root: Path, memory: _Memory) -> None:
        self.root = root
        self.memory = memory
        self.manifest: dict[str, Any] = {"input_sha256": "fake", "started_at": 0}
        self.refresh_run_id: str | None = None
        self.finished: dict[str, object] | None = None

    @property
    def crawl_id(self) -> str:
        return "fake-crawl"

    def write_manifest(self, value: dict[str, Any]) -> None:
        self.manifest = value

    def backup_to(self, destination: Path) -> None:
        return None

    def counts(self) -> dict[str, int]:
        return dict(
            Counter(f"url_status_{r.get('status')}" for r in self.memory.url_records.values())
        )

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]:
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

    def export_crawl(self, output: Path) -> None:
        return None

    def export_discovery(self, output: Path) -> None:
        return None

    def export_final(self, output: Path) -> None:
        return None


class FakeCrawlUnitOfWork:
    """A crawl state in memory, split into the repositories of a unit of work."""

    def __init__(self, root: Path = Path("state")) -> None:
        self.memory = _Memory()
        self.urls = FakeUrlRecords(self.memory)
        self.articles = FakeArticles(self.memory)
        self.owners = FakeContentOwners(self.memory)
        self.assets = FakeAssets(self.memory)
        self.families = FakeRouteFamilies(self.memory)
        self.hosts = FakeHostDiscovery(self.memory)
        self.refresh = FakeRefreshRuns()
        self.html = FakeRawHtml()
        self.run = FakeCrawlRun(root, self.memory)
        self.rolled_back = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self.rolled_back += 1
            raise

    def close(self) -> None:
        return None


class FakeRawHtml:
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
    def __init__(self, uow: FakeCrawlUnitOfWork, options: CrawlOptions) -> None:
        self.memory = uow.memory
        self.urls = uow.urls
        self.articles = uow.articles
        self.owners = uow.owners
        self.assets = uow.assets
        self.families = uow.families
        self.run = uow.run
        self.options = options
        self.files: list[dict[str, Any]] = uow.memory.files
        self.errors: list[dict[str, Any]] = uow.memory.errors
        self.discovered: list[dict[str, Any]] = uow.memory.discovered
        self.skipped: list[dict[str, Any]] = uow.memory.skipped
        self.url_records = uow.memory.url_records
        self.checkpoints: list[str] = []
        self.next_duplicate_of: str | None = None

    @property
    def discovered_assets(self) -> list[dict[str, Any]]:
        return [r for r in self.discovered if r.get("status") == "discovered_not_downloaded"]

    def record_scheduled(self, record: dict[str, Any]) -> None:
        self.urls.merge_url(record, completed=False)

    def record_discovered(self, record: dict[str, Any], *, completed: bool = True) -> None:
        self.discovered.append(record)
        self.urls.merge_url(record, completed=completed)

    def record_skipped(self, record: dict[str, Any]) -> None:
        self.skipped.append(record)
        self.urls.merge_url(record, completed=True)

    def store_html(self, url: str, body: bytes, **metadata: object) -> None:
        return None

    def record_rejected(self, record: dict[str, Any]) -> None:
        self.record_skipped(record)

    def record_navigation_skip(self, record: dict[str, Any]) -> None:
        if record["url"] not in self.memory.url_records:
            self.record_skipped(record)

    def record_absent(self, record: dict[str, Any]) -> None:
        self.discovered.append(record)
        self.urls.complete_url(record)

    def write_article(self, record: dict[str, Any]) -> dict[str, Any]:
        text = str(record.get("text") or "").strip()
        content_id = record.get("content_id") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec = dict(record)
        rec["content_id"] = content_id
        if self.next_duplicate_of is not None:
            rec["duplicate_of"] = self.next_duplicate_of
            self.next_duplicate_of = None
        self.urls.complete_url({"url": record["url"], "status": "extracted"}, article=rec)
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
        self.urls.complete_url(
            {"url": url, "final_url": final_url, "status": "file_saved", "asset_role": asset_role},
            file=rec,
        )
        return rec

    def write_error(self, record: dict[str, Any]) -> None:
        self.urls.complete_url(
            {
                "url": record["url"],
                "status": "failed",
                "reason": record.get("reason", record.get("error", "error")),
            },
            error=record,
        )

    def checkpoint(self, url: str) -> None:
        self.checkpoints.append(url)
        self.urls.checkpoint(url)


class FakeRefreshRuns:
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

    def __init__(self, uow: FakeCrawlUnitOfWork) -> None:
        self.urls = uow.urls
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
        self.urls.checkpoint(url)
