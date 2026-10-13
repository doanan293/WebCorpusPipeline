from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from data_pipeline.crawl.state.export import asset_metrics, export_final, skipped_reasons
from data_pipeline.crawl.state.resume import (
    normalize_legacy_semantic_config,
    validate_policy_migration,
)

if TYPE_CHECKING:
    from data_pipeline.crawl.frontier.assets import AssetRole


class CrawlState:
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

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        phase: Literal["discover", "crawl"],
        input_path: Path,
        semantic_config: dict[str, object],
        runtime_config: dict[str, object],
        resume: bool = False,
        allow_policy_migration: bool = False,
    ) -> CrawlState:
        root.mkdir(parents=True, exist_ok=True)
        input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        manifest_path = root / "manifest.json"
        lock_path = root / "run.lock"

        # Lock acquisition
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

        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not resume:
                    msg = f"crawl state already exists: {root}"
                    raise FileExistsError(msg)
                if manifest.get("state_version") != 4:
                    msg = "resume state version does not match saved crawl"
                    raise ValueError(msg)
                if manifest.get("phase") != phase:
                    msg = "resume phase does not match saved run"
                    raise ValueError(msg)
                if manifest.get("input_sha256") != input_sha256:
                    msg = "resume input does not match saved crawl"
                    raise ValueError(msg)
                saved_semantic = normalize_legacy_semantic_config(
                    manifest.get("semantic_config", {})
                )
                effective_semantic = normalize_legacy_semantic_config(semantic_config)
                if saved_semantic != effective_semantic:
                    if not allow_policy_migration:
                        msg = "resume semantic configuration does not match saved crawl"
                        raise ValueError(msg)
                    validate_policy_migration(saved_semantic, effective_semantic)
                    migrations = list(manifest.get("policy_migrations", []))
                    if not any(
                        entry.get("to_revision") == effective_semantic.get("access_policy_revision")
                        for entry in migrations
                        if isinstance(entry, dict)
                    ):
                        migrations.append(
                            {
                                "from_revision": saved_semantic.get("access_policy_revision", 1),
                                "to_revision": effective_semantic["access_policy_revision"],
                                "timestamp": time.time(),
                            }
                        )
                    manifest["policy_migrations"] = migrations
                    manifest["semantic_config"] = semantic_config
                history = manifest.get("runtime_history", [])
                if not history or history[-1] != runtime_config:
                    history.append(runtime_config)
                    manifest["runtime_history"] = history
                manifest["resumed_from_status"] = manifest.get("status")
                manifest["status"] = "running"

            else:
                if resume:
                    msg = f"cannot resume non-existent crawl state: {root}"
                    raise ValueError(msg)
                manifest = {
                    "state_version": 4,
                    "phase": phase,
                    "input_sha256": input_sha256,
                    "semantic_config": semantic_config,
                    "runtime_history": [runtime_config],
                    "status": "running",
                    "started_at": time.time(),
                }

            # Initialize SQLite
            db_path = root / "index.sqlite3"
            connection = sqlite3.connect(str(db_path), autocommit=True)
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA synchronous=NORMAL;")
            cls._init_db(connection)

            instance = cls(root, manifest, connection)
            instance.write_manifest(manifest)
            return instance
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise

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
            "CREATE TABLE IF NOT EXISTS content_owner (content_id TEXT PRIMARY KEY, url TEXT NOT NULL);"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metrics (name TEXT PRIMARY KEY, value INTEGER NOT NULL);"
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
        os.replace(temporary, self.manifest_path)

    def _merged_record(self, record: dict[str, object]) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT payload FROM url_records WHERE url = ?", (str(record["url"]),)
        ).fetchone()
        merged = json.loads(row[0]) if row else {}
        merged.update({key: value for key, value in record.items() if value is not None})
        return merged

    def merge_url(self, record: dict[str, object], *, completed: bool = False) -> dict[str, object]:
        existing_row = self.connection.execute(
            "SELECT payload, status, completed FROM url_records WHERE url = ?",
            (str(record["url"]),),
        ).fetchone()
        if (
            existing_row
            and existing_row[2]
            and existing_row[1] in {"extracted", "file_saved"}
            and record.get("status") not in {"extracted", "file_saved"}
        ):
            return json.loads(existing_row[0])

        merged = self._merged_record(record)
        if existing_row is None and self.refresh_run_id is not None:
            merged.setdefault("first_seen_run", self.refresh_run_id)
        url = str(merged["url"])
        payload = json.dumps(merged, ensure_ascii=False)
        status = str(merged.get("status", "discovered"))
        comp_val = 1 if (completed or merged.get("completed")) else 0
        self.connection.execute(
            """
            INSERT INTO url_records(url, payload, status, completed)
            VALUES (:url, :payload, :status, :completed)
            ON CONFLICT(url) DO UPDATE SET
              payload = excluded.payload,
              status = excluded.status,
              completed = MAX(url_records.completed, excluded.completed);
            """,
            {"url": url, "payload": payload, "status": status, "completed": comp_val},
        )
        return merged

    def _put_payload(self, table: str, record: dict[str, object]) -> None:
        url = str(record["url"])
        payload = json.dumps(record, ensure_ascii=False)
        self.connection.execute(
            f"""
            INSERT INTO {table} (url, payload) VALUES (?, ?)
            ON CONFLICT(url) DO UPDATE SET payload = excluded.payload;
            """,
            (url, payload),
        )

    def complete_url(
        self,
        disposition: dict[str, object],
        *,
        article: dict[str, object] | None = None,
        file: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
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

    def upsert_url(self, record: dict[str, object]) -> None:
        self.merge_url(record, completed=bool(record.get("completed")))

    def put_article(self, record: dict[str, object]) -> None:
        self._put_payload("articles", record)

    def put_file(self, record: dict[str, object]) -> None:
        self._put_payload("files", record)

    def put_error(self, record: dict[str, object]) -> None:
        self._put_payload("errors", record)

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
        old_text = json.loads(article_row[0]).get("text") if article_row else None
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
                        article["text"] = old_text
                        record["duplicate_of"] = None
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
        assert row is not None
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

    def put_target(self, record: dict[str, object]) -> None:
        url = str(record["url"])
        self.connection.execute(
            "INSERT OR REPLACE INTO targets (url, payload) VALUES (?, ?);",
            (url, json.dumps(record, sort_keys=True, ensure_ascii=False)),
        )

    def iter_targets(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute("SELECT payload FROM targets ORDER BY url;")
        for row in cursor:
            yield json.loads(row[0])

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

    def finish(
        self,
        status: str,
        exit_code: int,
        counts: dict[str, int],
        metrics: dict[str, object],
        truncation: dict[str, object] | None = None,
        *,
        discovery: dict[str, object] | None = None,
        frontier: dict[str, object] | None = None,
        host_completion: dict[str, dict[str, object]] | None = None,
        public_output: Path | None = None,
    ) -> None:
        finish_data: dict[str, object] = {
            "status": status,
            "exit_code": exit_code,
            "counts": counts,
            "metrics": metrics,
            "truncation": truncation,
            "truncation_reasons": sorted(list(truncation.keys()))
            if isinstance(truncation, dict)
            else [],
            "finished_at": time.time(),
        }
        if discovery is not None or self.manifest.get("phase") == "discover":
            finish_data["discovery"] = discovery
        if frontier is not None:
            finish_data["frontier"] = frontier
        elif "frontier" in metrics and isinstance(metrics["frontier"], dict):
            finish_data["frontier"] = metrics["frontier"]
        if self.manifest.get("phase") != "discover":
            finish_data["assets"] = asset_metrics(self)
            finish_data["skipped"] = skipped_reasons(self)
            finish_data["host_completion"] = host_completion or {}

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
            os.replace(tmp, pub_manifest)
            if self.manifest.get("phase") == "crawl":
                export_final(self, public_output)

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self._lock_acquired:
                self.lock_path.unlink(missing_ok=True)
                self._lock_acquired = False
