from __future__ import annotations

import json
from typing import TYPE_CHECKING

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


_ACCESS_GATE_REASONS = frozenset({"captcha_blocked", "login_required"})


def _scheduled(record: dict[str, object]) -> dict[str, object]:
    record["status"] = "scheduled"
    for key in ("final_url", "http_status", "reason"):
        record.pop(key, None)
    return record


def _requeue(state: CrawlState, records: list[tuple[str, dict[str, object]]]) -> int:
    """Put completed records back in the queue and forget their last error."""
    state.connection.execute("BEGIN IMMEDIATE;")
    try:
        for url, record in records:
            state.connection.execute(
                "UPDATE url_records SET payload = ?, status = 'scheduled', completed = 0 "
                "WHERE url = ?;",
                (json.dumps(record, ensure_ascii=False), url),
            )
            state.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
        state.connection.execute("COMMIT;")
    except Exception:
        state.connection.execute("ROLLBACK;")
        raise
    return len(records)


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
        if error.get("error") == "request_failed" and any(
            marker in message for marker in _RETRYABLE_REQUEST_FAILURE_MARKERS
        ):
            retryable.append((str(url), _scheduled(json.loads(record_payload))))
    return _requeue(state, retryable)


def requeue_access_gates(state: CrawlState) -> int:
    """Retry pages skipped behind a CAPTCHA or login gate."""
    rows = state.connection.execute(
        "SELECT url, payload FROM url_records WHERE completed = 1 AND status = 'skipped';"
    ).fetchall()
    recoverable = [
        (str(url), _scheduled(record))
        for url, payload in rows
        if (record := json.loads(payload)).get("reason") in _ACCESS_GATE_REASONS
    ]
    return _requeue(state, recoverable)


def restore_status(state: CrawlState) -> None:
    """Undo the 'running' mark of a resumed open when the run never started."""
    previous = state.manifest.get("resumed_from_status")
    if previous:
        state.manifest["status"] = previous
        state.write_manifest(state.manifest)
