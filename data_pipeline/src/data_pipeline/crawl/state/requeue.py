from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from data_pipeline.crawl.frontier.link_policy import classify_link
from data_pipeline.crawl.state.resume import POLICY_RETRY_REASONS, scheduled_retry

if TYPE_CHECKING:
    from data_pipeline.crawl.state.store import CrawlState

_RETRYABLE_REQUEST_FAILURE_MARKERS = (
    "an error occurred while connecting",
    "connection failure",
    "connection refused",
    "connection reset",
    "connection timed out",
    "connection to the other side was lost",
    "connection was closed cleanly",
    "dns lookup failed",
    "name or service not known",
    "network is unreachable",
    "no route to host",
    "temporary failure in name resolution",
    "timeout",
    "timed out",
    "took longer than",
)


def requeue_retryable_failures(state: CrawlState) -> int:
    rows = state.connection.execute(
        """
        SELECT url_records.url, url_records.payload, errors.payload
        FROM url_records
        JOIN errors ON errors.url = url_records.url
        WHERE url_records.completed = 1 AND url_records.status = 'failed';
        """
    ).fetchall()
    retryable: list[tuple[str, dict[str, object]]] = []
    for url, record_payload, error_payload in rows:
        error = json.loads(error_payload)
        message = str(error.get("message", "")).lower()
        if error.get("error") != "request_failed" or not any(
            marker in message for marker in _RETRYABLE_REQUEST_FAILURE_MARKERS
        ):
            continue
        record = json.loads(record_payload)
        record["status"] = "scheduled"
        for key in ("completed", "final_url", "http_status", "reason"):
            record.pop(key, None)
        retryable.append((str(url), record))

    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in retryable:
            state.connection.execute(
                """
                UPDATE url_records
                SET payload = ?, status = 'scheduled', completed = 0
                WHERE url = ?;
                """,
                (json.dumps(record, ensure_ascii=False), url),
            )
            state.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(retryable)


def requeue_query_variant_truncations(state: CrawlState) -> int:
    rows = state.connection.execute(
        """
        SELECT url, payload
        FROM url_records
        WHERE completed = 1 AND status = 'skipped';
        """
    ).fetchall()
    recoverable: list[tuple[str, dict[str, object]]] = []
    for url, payload in rows:
        record = json.loads(payload)
        if record.get("reason") != "query_variant_limit":
            continue
        classified = classify_link(str(url), source_url=str(url))
        if classified.kind not in {"content", "pagination"}:
            continue
        record.update(
            {
                "status": "scheduled",
                "seed_type": "recursive",
                "frontier_action": "scheduled",
                "discovery_source": "html_link",
                "response_purpose": "page",
                "explicit": False,
                "link_kind": classified.kind,
                "family_key": classified.family_key,
                "ordinal": classified.ordinal,
                "priority": 500 if classified.kind == "content" else 100,
            }
        )
        for key in ("completed", "final_url", "http_status", "reason"):
            record.pop(key, None)
        recoverable.append((str(url), record))

    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in recoverable:
            state.connection.execute(
                """
                UPDATE url_records
                SET payload = ?, status = 'scheduled', completed = 0
                WHERE url = ?;
                """,
                (json.dumps(record, ensure_ascii=False), url),
            )
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(recoverable)


def requeue_access_gates(state: CrawlState) -> int:
    rows = state.connection.execute(
        """
        SELECT url, payload
        FROM url_records
        WHERE completed = 1 AND status = 'skipped';
        """
    ).fetchall()
    recoverable: list[tuple[str, dict[str, object]]] = []
    for url, payload in rows:
        record = json.loads(payload)
        if record.get("reason") != "captcha_blocked":
            continue
        record["status"] = "scheduled"
        for key in ("completed", "final_url", "http_status", "reason"):
            record.pop(key, None)
        recoverable.append((str(url), record))

    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in recoverable:
            state.connection.execute(
                """
                UPDATE url_records
                SET payload = ?, status = 'scheduled', completed = 0
                WHERE url = ?;
                """,
                (json.dumps(record, ensure_ascii=False), url),
            )
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(recoverable)


def requeue_policy_skips(state: CrawlState, recursive_hostnames: frozenset[str]) -> int:
    rows = state.connection.execute(
        "SELECT url, payload FROM url_records WHERE completed = 1 AND status = 'skipped';"
    ).fetchall()
    recoverable: list[tuple[str, dict[str, object]]] = []
    for url, payload in rows:
        record = json.loads(payload)
        hostname = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
        if hostname not in recursive_hostnames:
            continue
        if record.get("reason") not in POLICY_RETRY_REASONS:
            continue
        recoverable.append((str(url), scheduled_retry(record)))

    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in recoverable:
            state.connection.execute(
                """
                UPDATE url_records
                SET payload = ?, status = 'scheduled', completed = 0
                WHERE url = ? AND status = 'skipped' AND completed = 1;
                """,
                (json.dumps(record, ensure_ascii=False), url),
            )
            state.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(recoverable)


def requeue_zero_content_bootstrap(state: CrawlState, recursive_hostnames: frozenset[str]) -> int:
    records = list(state.iter_url_records())
    extracted_hosts = {
        (urlsplit(str(record["url"])).hostname or "").lower().rstrip(".")
        for record in records
        if record.get("status") in {"extracted", "file_saved"}
    }
    by_url = {str(record["url"]): record for record in records}
    recoverable: list[tuple[str, dict[str, object]]] = []
    for hostname in sorted(recursive_hostnames - extracted_hosts):
        root = f"https://{hostname}/"
        record = by_url.get(root)
        if record is None:
            recoverable.append(
                (
                    root,
                    {
                        "url": root,
                        "status": "scheduled",
                        "seed_type": "recursive",
                        "frontier_action": "scheduled",
                        "discovery_source": "seed",
                        "response_purpose": "page",
                    },
                )
            )
        elif (
            record.get("status") == "skipped" and record.get("reason") in POLICY_RETRY_REASONS
        ) or (
            record.get("status") == "failed"
            and record.get("reason")
            in {
                "request_failed",
                "sitemap_parse_error",
            }
        ):
            recoverable.append((root, scheduled_retry(record)))

    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in recoverable:
            state.connection.execute(
                """
                INSERT INTO url_records(url, payload, status, completed)
                VALUES (?, ?, 'scheduled', 0)
                ON CONFLICT(url) DO UPDATE SET
                  payload = excluded.payload,
                  status = 'scheduled',
                  completed = 0;
                """,
                (url, json.dumps(record, ensure_ascii=False)),
            )
            state.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(recoverable)


def restore_status(state: CrawlState) -> None:
    """Undo the 'running' mark of a resumed open when the run never started."""
    previous = state.manifest.get("resumed_from_status")
    if previous:
        state.manifest["status"] = previous
        state.write_manifest(state.manifest)
