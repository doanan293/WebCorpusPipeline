from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from collections.abc import Iterable, Iterator

SUCCESS_OUTCOMES = frozenset({"new", "unchanged", "modified", "not_modified", "fetched"})
CONTINUABLE_RUN_STATUSES = frozenset({"running", "interrupted", "failed"})
FINISHED_RUN_STATUSES = ("complete", "complete_with_failures", "truncated")


class RefreshStore:
    """Persist refresh runs, their targets and detected changes in the crawl index."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
                run_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_targets (
                run_id TEXT NOT NULL,
                url TEXT NOT NULL,
                reason TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                outcome TEXT,
                error TEXT,
                PRIMARY KEY(run_id, url)
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_changes (
                run_id TEXT NOT NULL,
                url TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(run_id, url)
            );
            """
        )

    def start_run(self, run_id: str) -> None:
        self.connection.execute(
            "INSERT INTO refresh_runs (run_id, started_at, status) VALUES (?, ?, 'running');",
            (run_id, time.time()),
        )

    def resume_run(self, run_id: str) -> None:
        self.connection.execute(
            "UPDATE refresh_runs SET status = 'running', finished_at = NULL WHERE run_id = ?;",
            (run_id,),
        )

    def latest_run(self) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT run_id, status, started_at, finished_at
            FROM refresh_runs ORDER BY run_id DESC LIMIT 1;
            """
        ).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "status": row[1], "started_at": row[2], "finished_at": row[3]}

    def run_started_at(self, run_id: str) -> float:
        row = self.connection.execute(
            "SELECT started_at FROM refresh_runs WHERE run_id = ?;", (run_id,)
        ).fetchone()
        return float(row[0])

    def finish_run(self, run_id: str, status: str, payload: dict[str, object]) -> None:
        self.connection.execute(
            "UPDATE refresh_runs SET status = ?, finished_at = ?, payload = ? WHERE run_id = ?;",
            (status, time.time(), json.dumps(payload, ensure_ascii=False), run_id),
        )

    def completed_run_ids(self, limit: int) -> list[str]:
        rows = self.connection.execute(
            f"""
            SELECT run_id FROM refresh_runs
            WHERE status IN ({",".join("?" for _ in FINISHED_RUN_STATUSES)})
            ORDER BY run_id DESC LIMIT ?;
            """,
            (*FINISHED_RUN_STATUSES, limit),
        )
        return [str(row[0]) for row in rows]

    def add_targets(self, run_id: str, targets: Iterable[tuple[str, str]]) -> None:
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            self.connection.executemany(
                "INSERT OR IGNORE INTO refresh_targets (run_id, url, reason) VALUES (?, ?, ?);",
                ((run_id, url, reason) for url, reason in targets),
            )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def add_target(self, run_id: str, url: str, reason: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO refresh_targets (run_id, url, reason) VALUES (?, ?, ?);",
            (run_id, url, reason),
        )
        return cursor.rowcount == 1

    def pending_targets(self, run_id: str) -> list[tuple[str, str]]:
        rows = self.connection.execute(
            "SELECT url, reason FROM refresh_targets WHERE run_id = ? AND done = 0 ORDER BY url;",
            (run_id,),
        )
        return [(str(url), str(reason)) for url, reason in rows]

    def finish_target(self, run_id: str, url: str, outcome: str, error: str | None = None) -> None:
        self.connection.execute(
            """
            UPDATE refresh_targets SET done = 1, outcome = ?, error = ?
            WHERE run_id = ? AND url = ?;
            """,
            (outcome, error, run_id, url),
        )

    def record_change(self, run_id: str, change: dict[str, object]) -> None:
        payload = json.dumps({"run_id": run_id, **change}, ensure_ascii=False)
        self.connection.execute(
            """
            INSERT INTO refresh_changes (run_id, url, payload) VALUES (?, ?, ?)
            ON CONFLICT(run_id, url) DO UPDATE SET payload = excluded.payload;
            """,
            (run_id, str(change["url"]), payload),
        )

    def iter_changes(self, run_id: str) -> Iterator[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT payload FROM refresh_changes WHERE run_id = ? ORDER BY url;", (run_id,)
        ).fetchall()
        for (payload,) in rows:
            yield json.loads(payload)

    def target_summary(self, run_id: str) -> dict[str, dict[str, int]]:
        reasons: Counter[str] = Counter()
        outcomes: Counter[str] = Counter()
        for reason, outcome in self.connection.execute(
            "SELECT reason, outcome FROM refresh_targets WHERE run_id = ?;", (run_id,)
        ):
            reasons[str(reason)] += 1
            outcomes[str(outcome or "pending")] += 1
        return {
            "targets_by_reason": dict(sorted(reasons.items())),
            "outcomes": dict(sorted(outcomes.items())),
        }

    def failure_counts(self, run_id: str, truncation_reasons: Iterable[str]) -> tuple[int, int]:
        successes = sorted(SUCCESS_OUTCOMES)
        failed_targets = self.connection.execute(
            f"""
            SELECT count(*) FROM refresh_targets
            WHERE run_id = ? AND done = 1
              AND outcome NOT IN ({",".join("?" for _ in successes)});
            """,
            (run_id, *successes),
        ).fetchone()[0]
        failed_new = self.connection.execute(
            """
            SELECT count(*) FROM url_records
            WHERE status = 'failed' AND json_extract(payload, '$.first_seen_run') = ?;
            """,
            (run_id,),
        ).fetchone()[0]
        reasons = sorted(truncation_reasons)
        truncated = 0
        if reasons:
            truncated = self.connection.execute(
                f"""
                SELECT count(*) FROM url_records
                WHERE json_extract(payload, '$.first_seen_run') = ?
                  AND json_extract(payload, '$.reason') IN ({",".join("?" for _ in reasons)});
                """,
                (run_id, *reasons),
            ).fetchone()[0]
        return int(failed_targets) + int(failed_new), int(truncated)
