from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from data_pipeline.infrastructure.crawl.sqlite.transaction import immediate_transaction

if TYPE_CHECKING:
    from data_pipeline.infrastructure.crawl.sqlite.run import SqliteCrawlRun

_TERMINAL_STATUSES = frozenset({"extracted", "file_saved"})
_FINAL_STATUSES = _TERMINAL_STATUSES | {"failed", "absent"}


class SqliteUrlRecords:
    """The ``UrlRecords`` port over ``url_records`` (and the ``errors`` rows that go with it)."""

    def __init__(self, connection: sqlite3.Connection, run: SqliteCrawlRun) -> None:
        self.connection = connection
        self._run = run

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
        if existing_row is None and self._run.refresh_run_id is not None:
            merged.setdefault("first_seen_run", self._run.refresh_run_id)
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
        with immediate_transaction(self.connection):
            for url, record in records:
                self.connection.execute(
                    "UPDATE url_records SET payload = ?, status = 'scheduled', completed = 0 "
                    "WHERE url = ?;",
                    (json.dumps(record, ensure_ascii=False), url),
                )
                self.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        return len(records)
