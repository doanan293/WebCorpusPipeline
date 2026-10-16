from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from data_pipeline.infrastructure.crawl.sqlite.articles import SqliteArticles
from data_pipeline.infrastructure.crawl.sqlite.assets import SqliteAssets
from data_pipeline.infrastructure.crawl.sqlite.content_owners import SqliteContentOwners
from data_pipeline.infrastructure.crawl.sqlite.host_discovery import SqliteHostDiscovery
from data_pipeline.infrastructure.crawl.sqlite.html_store import HtmlStore
from data_pipeline.infrastructure.crawl.sqlite.refresh_store import RefreshStore
from data_pipeline.infrastructure.crawl.sqlite.resume import manifest_for_open
from data_pipeline.infrastructure.crawl.sqlite.route_families import SqliteRouteFamilies
from data_pipeline.infrastructure.crawl.sqlite.run import SqliteCrawlRun
from data_pipeline.infrastructure.crawl.sqlite.transaction import immediate_transaction
from data_pipeline.infrastructure.crawl.sqlite.url_records import SqliteUrlRecords


def _acquire_lock(root: Path) -> Path:
    """Create ``run.lock`` holding this PID; refuse when another live crawl owns the state."""
    lock_path = root / "run.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            lock_path.unlink(missing_ok=True)
        else:
            msg = f"crawl is already running with PID {pid}"
            raise RuntimeError(msg)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        msg = f"crawl state is locked: {root}"
        raise RuntimeError(msg) from exc
    os.write(fd, f"{os.getpid()}\n".encode())
    os.close(fd)
    return lock_path


class SqliteCrawlUnitOfWork:
    """One open crawl state: ``index.sqlite3`` behind ``run.lock``, split into repositories.

    Every repository shares the one connection, so ``transaction()`` covers all of them.
    """

    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        connection: sqlite3.Connection,
        html_dir: Path,
    ) -> None:
        self.connection = connection
        self.lock_path = root / "run.lock"
        self._lock_acquired = True
        self.run = SqliteCrawlRun(root, manifest, connection)
        self.urls = SqliteUrlRecords(connection, self.run)
        self.articles = SqliteArticles(connection)
        self.owners = SqliteContentOwners(connection)
        self.assets = SqliteAssets(connection)
        self.families = SqliteRouteFamilies(connection)
        self.hosts = SqliteHostDiscovery(connection)
        self.refresh = RefreshStore(connection)
        self.html = HtmlStore(html_dir, connection)

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        phase: Literal["discover", "crawl"],
        input_path: Path,
        semantic_config: Mapping[str, object],
        runtime_config: Mapping[str, object],
        resume: bool = False,
        html_dir: Path | None = None,
    ) -> SqliteCrawlUnitOfWork:
        root.mkdir(parents=True, exist_ok=True)
        input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        lock_path = _acquire_lock(root)
        try:
            manifest = manifest_for_open(
                root,
                resume=resume,
                phase=phase,
                input_sha256=input_sha256,
                semantic_config=semantic_config,
                runtime_config=runtime_config,
            )
            connection = sqlite3.connect(str(root / "index.sqlite3"), autocommit=True)
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA synchronous=NORMAL;")
            cls._init_db(connection)
            instance = cls(root, manifest, connection, html_dir or root.parent / "html")
            instance.run.write_manifest(manifest)
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        else:
            return instance

    @classmethod
    def open_existing(cls, root: Path, *, html_dir: Path | None = None) -> SqliteCrawlUnitOfWork:
        """Open a finished crawl's state as it is, holding its lock, without a new manifest."""
        lock_path = _acquire_lock(root)
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            connection = sqlite3.connect(str(root / "index.sqlite3"), autocommit=True)
            connection.execute("PRAGMA journal_mode=WAL;")
            cls._init_db(connection)
            instance = cls(root, manifest, connection, html_dir or root.parent / "html")
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        return instance

    @staticmethod
    def _init_db(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS url_records (
                url TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS targets (url TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS articles (url TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS files (url TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS errors (url TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS content_owner (content_id TEXT PRIMARY KEY, url TEXT NOT "
            "NULL);"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_pages (
                url TEXT PRIMARY KEY,
                final_url TEXT NOT NULL,
                status INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                body_sha256 TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS host_discovery (
                hostname TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS route_families (
                family_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_referrers (
                url TEXT NOT NULL,
                page_url TEXT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY(url, page_url, role)
            );
            """
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with immediate_transaction(self.connection):
            yield

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self._lock_acquired:
                self.lock_path.unlink(missing_ok=True)
                self._lock_acquired = False
