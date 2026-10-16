from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from data_pipeline.domain.crawl.models import DUPLICATE_DROPPED_FIELDS, RouteFamilyState
from data_pipeline.infrastructure.crawl.sqlite import discovery_rows, export
from data_pipeline.infrastructure.crawl.sqlite.export import asset_metrics, skipped_reasons
from data_pipeline.infrastructure.crawl.sqlite.html_store import HtmlStore
from data_pipeline.infrastructure.crawl.sqlite.refresh_store import RefreshStore
from data_pipeline.infrastructure.crawl.sqlite.resume import manifest_for_open

if TYPE_CHECKING:
    from data_pipeline.domain.crawl.frontier.assets import AssetRole

_TERMINAL_STATUSES = frozenset({"extracted", "file_saved"})
_FINAL_STATUSES = _TERMINAL_STATUSES | {"failed", "absent"}


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


class SqliteCrawlState:
    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        connection: sqlite3.Connection,
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.manifest_path = root / "manifest.json"
        self.lock_path = root / "run.lock"
        self.connection = connection
        self._lock_acquired = True
        self.refresh_run_id: str | None = None
        self._refresh_store: RefreshStore | None = None

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
    ) -> SqliteCrawlState:
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
            instance = cls(root, manifest, connection)
            instance.write_manifest(manifest)
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        else:
            return instance

    @classmethod
    def open_existing(cls, root: Path) -> SqliteCrawlState:
        """Open a finished crawl's state as it is, holding its lock, without a new manifest."""
        lock_path = _acquire_lock(root)
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            connection = sqlite3.connect(str(root / "index.sqlite3"), autocommit=True)
            connection.execute("PRAGMA journal_mode=WAL;")
            cls._init_db(connection)
            instance = cls(root, manifest, connection)
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

    def write_manifest(self, value: dict[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    @staticmethod
    def _merged_record(
        existing: dict[str, object], record: Mapping[str, object]
    ) -> dict[str, object]:
        """Overlay ``record`` on ``existing``; an explicit None deletes the key."""
        merged = dict(existing)
        for key, value in record.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if record.get("status") in _TERMINAL_STATUSES:
            for key in ("reason", "duplicate_of"):
                if key not in record:
                    merged.pop(key, None)
        return merged

    def merge_url(
        self,
        record: Mapping[str, object],
        *,
        completed: bool = False,
        keep_scheduled: bool = False,
    ) -> dict[str, object]:
        """Write a URL record; ``keep_scheduled`` leaves a pending scheduled row untouched."""
        url = str(record["url"])
        existing_row = self.connection.execute(
            "SELECT payload, status, completed FROM url_records WHERE url = ?", (url,)
        ).fetchone()
        if existing_row and (
            (
                existing_row[2]
                and existing_row[1] in _TERMINAL_STATUSES
                and record.get("status") not in _TERMINAL_STATUSES
            )
            or (keep_scheduled and not existing_row[2] and existing_row[1] == "scheduled")
        ):
            return json.loads(existing_row[0])

        merged = self._merged_record(json.loads(existing_row[0]) if existing_row else {}, record)
        if existing_row is None and self.refresh_run_id is not None:
            merged.setdefault("first_seen_run", self.refresh_run_id)
        payload = json.dumps(merged, ensure_ascii=False)
        status = str(merged.get("status", "discovered"))
        comp_val = 1 if completed else 0
        self.connection.execute(
            """
            INSERT INTO url_records(url, payload, status, completed)
            VALUES (:url, :payload, :status, :completed)
            ON CONFLICT(url) DO UPDATE SET
              payload = excluded.payload,
              status = excluded.status,
              -- Scheduling reopens a URL an earlier skip had closed, so a resume fetches it.
              completed = CASE WHEN excluded.status = 'scheduled' THEN excluded.completed
                               ELSE MAX(url_records.completed, excluded.completed) END;
            """,
            {"url": url, "payload": payload, "status": status, "completed": comp_val},
        )
        if status != "failed":
            self.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        return merged

    def _put_payload(self, table: str, record: Mapping[str, object]) -> None:
        url = str(record["url"])
        payload = json.dumps(dict(record), ensure_ascii=False)
        self.connection.execute(
            f"""
            INSERT INTO {table} (url, payload) VALUES (?, ?)
            ON CONFLICT(url) DO UPDATE SET payload = excluded.payload;
            """,  # noqa: S608 -- only fixed table names and "?" placeholders are interpolated
            (url, payload),
        )

    def complete_url(
        self,
        disposition: Mapping[str, object],
        *,
        article: Mapping[str, object] | None = None,
        file: Mapping[str, object] | None = None,
        error: Mapping[str, object] | None = None,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            if article is not None:
                self._put_payload("articles", article)
            if file is not None:
                self._put_payload("files", file)
            if error is not None:
                self._put_payload("errors", error)
            self.merge_url(disposition, completed=True)
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def upsert_url(self, record: Mapping[str, object], *, completed: bool = False) -> None:
        self.merge_url(record, completed=completed)

    def put_error(self, record: Mapping[str, object]) -> None:
        self._put_payload("errors", record)

    def is_finished(self, url: str) -> bool:
        """True once the URL reached a final status, or was fetched and then skipped."""
        row = self.connection.execute(
            "SELECT payload, status, completed FROM url_records WHERE url = ?;", (url,)
        ).fetchone()
        if row is None or not row[2]:
            return False
        if row[1] in _FINAL_STATUSES:
            return True
        return row[1] == "skipped" and json.loads(row[0]).get("frontier_action") == "scheduled"

    def insert_url_if_absent(self, record: Mapping[str, object]) -> None:
        """Write a completed record for a URL the crawl has not seen; never touch a known URL."""
        self.connection.execute(
            """
            INSERT INTO url_records(url, payload, status, completed) VALUES (?, ?, ?, 1)
            ON CONFLICT(url) DO NOTHING;
            """,
            (
                str(record["url"]),
                json.dumps(dict(record), ensure_ascii=False),
                str(record.get("status", "discovered")),
            ),
        )

    def is_complete(self, url: str) -> bool:
        cursor = self.connection.execute("SELECT completed FROM url_records WHERE url = ?;", (url,))
        row = cursor.fetchone()
        return bool(row and row[0] == 1)

    def checkpoint(self, url: str) -> None:
        self.connection.execute("UPDATE url_records SET completed = 1 WHERE url = ?;", (url,))
        self.connection.commit()

    @property
    def crawl_id(self) -> str:
        seed = f"{self.manifest['input_sha256']}:{self.manifest['started_at']!r}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def url_record(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT payload FROM url_records WHERE url = ?;", (url,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def update_url_fields(self, url: str, fields: Mapping[str, object]) -> None:
        record = self.url_record(url)
        if record is None:
            return
        record.update(fields)
        self.connection.execute(
            "UPDATE url_records SET payload = ? WHERE url = ?;",
            (json.dumps(record, ensure_ascii=False), url),
        )

    def article(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT payload FROM articles WHERE url = ?;", (url,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def mark_absent(self, url: str) -> None:
        """Record that a page is gone; it no longer appears in final/."""
        record = self.url_record(url)
        if record is None:
            return
        record["status"] = "absent"
        self.connection.execute(
            "UPDATE url_records SET payload = ?, status = 'absent', completed = 1 WHERE url = ?;",
            (json.dumps(record, ensure_ascii=False), url),
        )
        self.connection.commit()

    def release_content_owner(self, url: str, content_id: str) -> None:
        """Hand ownership of content_id from url to its first duplicate, if any."""
        owner = self.connection.execute(
            "SELECT url FROM content_owner WHERE content_id = ?;", (content_id,)
        ).fetchone()
        if owner is None or owner[0] != url:
            return
        article_row = self.connection.execute(
            "SELECT payload FROM articles WHERE url = ?;", (url,)
        ).fetchone()
        old_article: dict[str, Any] = json.loads(article_row[0]) if article_row else {}
        owner_content = {
            field: old_article[field]
            for field in ("text", *DUPLICATE_DROPPED_FIELDS)
            if field in old_article
        }
        dependents = [
            str(row[0])
            for row in self.connection.execute(
                """
                SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
                WHERE json_extract(a.payload, '$.duplicate_of') = ?
                  AND json_extract(r.payload, '$.content_id') = ?
                ORDER BY a.url;
                """,
                (url, content_id),
            )
        ]
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            if not dependents:
                self.connection.execute(
                    "DELETE FROM content_owner WHERE content_id = ?;", (content_id,)
                )
            else:
                new_owner = dependents[0]
                self.connection.execute(
                    "UPDATE content_owner SET url = ? WHERE content_id = ?;",
                    (new_owner, content_id),
                )
                for dependent in dependents:
                    article = json.loads(
                        self.connection.execute(
                            "SELECT payload FROM articles WHERE url = ?;", (dependent,)
                        ).fetchone()[0]
                    )
                    record = self.url_record(dependent) or {"url": dependent}
                    if dependent == new_owner:
                        article.pop("duplicate_of", None)
                        article.update(owner_content)
                        record.pop("duplicate_of", None)
                    else:
                        article["duplicate_of"] = new_owner
                        record["duplicate_of"] = new_owner
                    self.connection.execute(
                        "UPDATE articles SET payload = ? WHERE url = ?;",
                        (json.dumps(article, ensure_ascii=False), dependent),
                    )
                    self.connection.execute(
                        "UPDATE url_records SET payload = ? WHERE url = ?;",
                        (json.dumps(record, ensure_ascii=False), dependent),
                    )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self.connection.backup(target)
        finally:
            target.close()

    def claim_content(self, content_id: str, url: str) -> str:
        self.connection.execute(
            """
            INSERT INTO content_owner (content_id, url)
            VALUES (?, ?)
            ON CONFLICT(content_id) DO NOTHING;
            """,
            (content_id, url),
        )
        self.connection.commit()
        cursor = self.connection.execute(
            "SELECT url FROM content_owner WHERE content_id = ?;", (content_id,)
        )
        row = cursor.fetchone()
        if row is None:
            msg = f"content owner missing after claim: {content_id}"
            raise RuntimeError(msg)
        return str(row[0])

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in self.connection.execute(
            "SELECT status, count(*) FROM url_records GROUP BY status;"
        ):
            result[f"url_status_{row[0]}"] = int(row[1])

        cursor = self.connection.execute("SELECT count(*) FROM url_records;")
        result["total_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM url_records WHERE completed = 1;")
        result["completed_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM articles;")
        result["articles"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM files;")
        result["files"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM errors;")
        result["errors"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM targets;")
        result["targets"] = int(cursor.fetchone()[0])

        return result

    def put_target(self, record: Mapping[str, object]) -> None:
        url = str(record["url"])
        self.connection.execute(
            "INSERT OR REPLACE INTO targets (url, payload) VALUES (?, ?);",
            (url, json.dumps(dict(record), sort_keys=True, ensure_ascii=False)),
        )

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None:
        self.connection.execute(
            """
            INSERT INTO asset_referrers (url, page_url, role)
            VALUES (?, ?, ?)
            ON CONFLICT(url, page_url, role) DO NOTHING;
            """,
            (url, page_url, role),
        )
        cursor = self.connection.execute("SELECT payload FROM files WHERE url = ?;", (url,))
        row = cursor.fetchone()
        if row:
            payload = json.loads(row[0])
            payload["referring_pages"] = list(self.asset_referrers(url))
            new_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            self.connection.execute(
                "UPDATE files SET payload = ? WHERE url = ?;",
                (new_payload, url),
            )

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        cursor = self.connection.execute(
            "SELECT DISTINCT page_url FROM asset_referrers WHERE url = ? ORDER BY page_url ASC;",
            (url,),
        )
        return tuple(row[0] for row in cursor.fetchall())

    def iter_url_records(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute("SELECT payload FROM url_records ORDER BY url;")
        for row in cursor:
            yield json.loads(row[0])

    def iter_pending_scheduled_records(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute(
            """
            SELECT payload
            FROM url_records
            WHERE completed = 0 AND status = 'scheduled'
            ORDER BY url;
            """
        )
        for row in cursor:
            record = json.loads(row[0])
            if record.get("frontier_action") == "scheduled":
                yield record

    def finish(  # noqa: PLR0913 -- one keyword per manifest field recorded at the end of a run
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
        finish_data: dict[str, object] = {
            "status": status,
            "exit_code": exit_code,
            "counts": counts,
            "metrics": dict(metrics),
            "truncation": dict(truncation) if truncation is not None else None,
            "truncation_reasons": sorted(truncation) if truncation is not None else [],
            "finished_at": time.time(),
        }
        if discovery is not None or self.manifest.get("phase") == "discover":
            finish_data["discovery"] = dict(discovery) if discovery is not None else None
        if frontier is not None:
            finish_data["frontier"] = dict(frontier)
        elif "frontier" in metrics and isinstance(metrics["frontier"], dict):
            finish_data["frontier"] = metrics["frontier"]
        if self.manifest.get("phase") != "discover":
            finish_data["assets"] = asset_metrics(self)
            finish_data["skipped"] = skipped_reasons(self)
            finish_data["host_completion"] = {
                k: dict(v) for k, v in (host_completion or {}).items()
            }

        self.manifest.update(finish_data)
        self.write_manifest(self.manifest)
        if public_output is not None:
            public_output.mkdir(parents=True, exist_ok=True)
            pub_manifest = public_output / "manifest.json"
            tmp = pub_manifest.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            tmp.replace(pub_manifest)
            if self.manifest.get("phase") == "crawl":
                export.export_final(self, public_output)

    # Views and writes the application layer names instead of running SQL ----------------

    def url_status(self, url: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM url_records WHERE url = ?;", (url,)
        ).fetchone()
        return str(row[0]) if row else None

    def parsed_probes(self) -> list[tuple[str, str]]:
        """(url, purpose) of robots and sitemap responses that parsed and were checkpointed."""
        # A robots/sitemap response that parsed successfully keeps status 'scheduled'
        # and is only checkpointed; absent or failed ones get another status.
        rows = self.connection.execute(
            """
            SELECT url, json_extract(payload, '$.response_purpose')
            FROM url_records
            WHERE completed = 1 AND status = 'scheduled'
              AND json_extract(payload, '$.response_purpose') IN ('robots', 'sitemap')
            ORDER BY url;
            """
        ).fetchall()
        return [(str(url), str(purpose)) for url, purpose in rows]

    def navigation_pages(self) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
            WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'navigation'
            ORDER BY a.url;
            """
        )
        return [str(row[0]) for row in rows]

    def pagination_members(self) -> list[tuple[str, str, str | None]]:
        """(family_key, url, discovered_from) of extracted paginated pages, in page order."""
        rows = self.connection.execute(
            """
            SELECT json_extract(payload, '$.family_key'), url,
                   json_extract(payload, '$.discovered_from')
            FROM url_records
            WHERE status = 'extracted' AND json_extract(payload, '$.family_key') IS NOT NULL
            ORDER BY json_extract(payload, '$.family_key'),
                     json_extract(payload, '$.ordinal') IS NULL,
                     json_extract(payload, '$.ordinal'),
                     url;
            """
        ).fetchall()
        return [(str(key), str(url), str(parent) if parent else None) for key, url, parent in rows]

    def recent_content_candidates(self) -> list[tuple[str, object, object, object]]:
        """(url, published_at, modified_at, first_seen_run) of extracted content pages."""
        rows = self.connection.execute(
            """
            SELECT a.url,
                   json_extract(a.payload, '$.published_at'),
                   json_extract(a.payload, '$.modified_at'),
                   json_extract(r.payload, '$.first_seen_run')
            FROM articles AS a JOIN url_records AS r ON r.url = a.url
            WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'content';
            """
        )
        return [(str(url), published, modified, first) for url, published, modified, first in rows]

    def failed_records(self) -> list[tuple[str, dict[str, object], dict[str, object]]]:
        """(url, record, error) of every completed URL that failed with a recorded error."""
        rows = self.connection.execute(
            """
            SELECT url_records.url, url_records.payload, errors.payload
            FROM url_records
            JOIN errors ON errors.url = url_records.url
            WHERE url_records.completed = 1 AND url_records.status = 'failed';
            """
        ).fetchall()
        return [(str(url), json.loads(record), json.loads(error)) for url, record, error in rows]

    def skipped_records(self) -> list[tuple[str, dict[str, object]]]:
        rows = self.connection.execute(
            "SELECT url, payload FROM url_records WHERE completed = 1 AND status = 'skipped';"
        ).fetchall()
        return [(str(url), json.loads(payload)) for url, payload in rows]

    def requeue(self, records: list[tuple[str, dict[str, object]]]) -> int:
        """Put completed records back in the queue and forget their last error."""
        with self.transaction():
            for url, record in records:
                self.connection.execute(
                    "UPDATE url_records SET payload = ?, status = 'scheduled', completed = 0 "
                    "WHERE url = ?;",
                    (json.dumps(record, ensure_ascii=False), url),
                )
                self.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        return len(records)

    def file_record(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT payload FROM files WHERE url = ?;", (url,)).fetchone()
        return json.loads(row[0]) if row else None

    def reextract_candidates(self) -> list[tuple[str, object]]:
        """(url, content_id) of extracted pages whose raw HTML is stored."""
        rows = self.connection.execute(
            """
            SELECT r.url, json_extract(r.payload, '$.content_id')
            FROM url_records AS r JOIN raw_pages AS p ON p.url = r.url
            WHERE r.status = 'extracted' ORDER BY r.url;
            """
        ).fetchall()
        return [(str(url), content_id) for url, content_id in rows]

    def set_article_payload(self, url: str, article: Mapping[str, object]) -> None:
        self.connection.execute(
            "UPDATE articles SET payload = ? WHERE url = ?;",
            (json.dumps(dict(article), ensure_ascii=False), url),
        )

    def content_owners(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT content_id, url FROM content_owner;")
        return {str(content_id): str(url) for content_id, url in rows}

    def replace_content_owners(self, urls: Iterable[str], owners: Mapping[str, str]) -> None:
        """Drop the ownership rows of ``urls``, then store ``owners`` (content_id -> url)."""
        self.connection.executemany(
            "DELETE FROM content_owner WHERE url = ?;", ((url,) for url in urls)
        )
        self.connection.executemany(
            "INSERT OR REPLACE INTO content_owner (content_id, url) VALUES (?, ?);",
            owners.items(),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            yield
            self.connection.execute("COMMIT;")
        except BaseException:
            self.connection.execute("ROLLBACK;")
            raise

    def raw_html_store(self, directory: Path) -> HtmlStore:
        return HtmlStore(directory, self.connection)

    def refresh_store(self) -> RefreshStore:
        if self._refresh_store is None:
            self._refresh_store = RefreshStore(self.connection)
        return self._refresh_store

    def export_crawl(self, output: Path) -> None:
        export.export_crawl(self, output)

    def export_discovery(self, output: Path) -> None:
        export.export_discovery(self, output)

    def export_final(self, output: Path) -> None:
        export.export_final(self, output)

    def iter_final_articles(self, urls: Iterable[str] | None = None) -> Iterator[tuple[str, str]]:
        return export.iter_final_articles(self, urls)

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]:
        return export.reason_counts(self, reasons)

    def route_families(self) -> Iterator[RouteFamilyState]:
        return discovery_rows.iter_route_families(self)

    def put_route_family(self, family: RouteFamilyState) -> None:
        discovery_rows.put_route_family(self, family)

    def host_discovery(self) -> Iterator[dict[str, object]]:
        return discovery_rows.iter_host_discovery(self)

    def upsert_host_discovery(self, record: Mapping[str, object]) -> None:
        discovery_rows.upsert_host_discovery(self, record)

    def host_completion(self, hostnames: frozenset[str]) -> dict[str, dict[str, object]]:
        return discovery_rows.host_completion(self, hostnames)

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self._lock_acquired:
                self.lock_path.unlink(missing_ok=True)
                self._lock_acquired = False
