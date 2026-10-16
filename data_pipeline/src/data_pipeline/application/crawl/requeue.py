from __future__ import annotations

from data_pipeline.application.crawl.ports import CrawlStateStore

_RETRYABLE_REQUEST_FAILURE_MARKERS = (
    # A browser render that ran out of time under load; it usually works on a retry.
    "page.goto: timeout",
    "timeout 30000ms exceeded",
    "timeout 60000ms exceeded",
    "rendered body did not stabilize",
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


def requeue_retryable_failures(state: CrawlStateStore) -> int:
    retryable: list[tuple[str, dict[str, object]]] = []
    for url, record, error in state.failed_records():
        message = str(error.get("message", "")).lower()
        if error.get("error") == "request_failed" and any(
            marker in message for marker in _RETRYABLE_REQUEST_FAILURE_MARKERS
        ):
            retryable.append((url, _scheduled(record)))
    return state.requeue(retryable)


def requeue_access_gates(state: CrawlStateStore) -> int:
    """Retry pages skipped behind a CAPTCHA or login gate."""
    recoverable = [
        (url, _scheduled(record))
        for url, record in state.skipped_records()
        if record.get("reason") in _ACCESS_GATE_REASONS
    ]
    return state.requeue(recoverable)


def restore_status(state: CrawlStateStore) -> None:
    """Undo the 'running' mark of a resumed open when the run never started."""
    previous = state.manifest.get("resumed_from_status")
    if previous:
        state.manifest["status"] = previous
        state.write_manifest(state.manifest)
