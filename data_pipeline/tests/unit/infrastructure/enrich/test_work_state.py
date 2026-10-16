from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from data_pipeline.domain.enrich.models import (
    DownloadedFile,
    DownloadJob,
    ParseResult,
    SourceProgress,
)
from data_pipeline.infrastructure.enrich.sqlite.unit_of_work import SqliteEnrichUnitOfWork


def test_requeue_cloud_parses_forgets_only_results_that_used_the_cloud(tmp_path: Path) -> None:
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as state:
        local_only = ParseResult("a", [], "extracted", [], {"local": "pymupdf"})
        with_cloud = ParseResult("b", [], "extracted", [], {"local": "pymupdf", "llamaparse": "x"})
        state.parses.mark_parse("https://a.test/local.pdf", local_only)
        state.parses.mark_parse(
            "https://a.test/cloud.pdf", with_cloud, tmp_path / "cache" / "k.json", used_cloud=True
        )

        assert state.parses.requeue_cloud_parses() == 1
        remaining = [row["url"] for row in state.conn.execute("SELECT url FROM parse_results")]

    assert remaining == ["https://a.test/local.pdf"]


def test_reparse_cloud_deletes_cache_files(tmp_path: Path) -> None:
    cached = tmp_path / "work" / "cache" / "k.json"
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as state:
        cached.parent.mkdir(parents=True)
        cached.write_text("{}", encoding="utf-8")
        result = ParseResult("b", [], "extracted", [], {"llamaparse": "x"})
        state.parses.mark_parse("https://a.test/cloud.pdf", result, cached, used_cloud=True)

        assert state.parses.requeue_cloud_parses() == 1

    assert not cached.exists()


def test_summary_counts_cloud_failed(tmp_path: Path) -> None:
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as state:
        failed = ParseResult("a", [], "cloud_failed", ["transient: boom"], {})
        state.parses.mark_parse("https://a.test/x.pdf", failed, used_cloud=True)

        assert state.summary().cloud_failed == 1


def test_cloud_failed_is_retried_next_run(tmp_path: Path) -> None:
    url = "https://a.test/x.pdf"
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as state:
        state.inventory.add_inventory_batch([url], [], SourceProgress("s", 0, 0, 0, done=True))
        state.downloads.mark_downloaded(
            DownloadedFile(url, tmp_path / "x.pdf", "sha", 1, "application/pdf", url)
        )
        state.parses.mark_parse(
            url, ParseResult("sha", [], "cloud_failed", [], {}), used_cloud=True
        )

        assert [file.url for file in state.parses.pending_parse()] == [url]


class RecordingLock:
    """A re-entrant lock that counts how often it was taken."""

    def __init__(self) -> None:
        self.inner = threading.RLock()
        self.taken = 0

    def __enter__(self) -> None:
        self.inner.acquire()
        self.taken += 1

    def __exit__(self, *exc: object) -> None:
        self.inner.release()


def test_all_writes_hold_write_lock(tmp_path: Path) -> None:
    url = "https://a.test/x.pdf"
    file = DownloadedFile(url, tmp_path / "x.pdf", "sha", 1, "application/pdf", url)
    writes: list[Callable[[SqliteEnrichUnitOfWork], object]] = [
        lambda s: s.inventory.add_inventory_batch([url], [], SourceProgress("s", 0, 0, 0)),
        lambda s: s.inventory.delete_refs_from(["https://a.test/"]),
        lambda s: s.downloads.claim_download(),
        lambda s: s.downloads.checkpoint_download(DownloadJob(url, tmp_path / "x.part")),
        lambda s: s.downloads.mark_finalizing(file),
        lambda s: s.downloads.mark_downloaded(file),
        lambda s: s.parses.mark_parse(url, ParseResult("sha", [], "extracted", [], {})),
        lambda s: s.parses.requeue_cloud_parses(),
        lambda s: s.failures.record_failure(url, "parse", "boom"),
        lambda s: s.recover(),
        lambda s: s.downloads.reset_failed_downloads(),
        lambda s: s.parses.save_cloud_job("r", url),
    ]
    with SqliteEnrichUnitOfWork.open(tmp_path / "work", "c1") as state:
        lock = RecordingLock()
        for holder in (state, state.inventory, state.downloads, state.parses, state.failures):
            holder._write_lock = lock
        for write in writes:
            before = lock.taken
            write(state)
            assert lock.taken > before, write
