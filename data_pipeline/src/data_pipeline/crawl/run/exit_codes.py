from __future__ import annotations

from typing import Literal

TRUNCATION_REASONS = frozenset(
    {
        "file_size_limit",
        "storage_budget",
        "query_variant_limit",
        "host_url_limit",
        "total_url_limit",
    }
)


def finish_reason_to_exit(
    finish_reason: str,
    *,
    queued: int = 0,
    failed: int = 0,
    truncated: int = 0,
) -> tuple[
    Literal[
        "complete",
        "complete_with_failures",
        "truncated",
        "interrupted",
        "failed",
    ],
    int,
]:
    if finish_reason in {"shutdown", "signal"}:
        return "interrupted", 130
    if queued > 0 and finish_reason != "finished":
        return "interrupted", 130
    if truncated > 0:
        return "truncated", 4
    if failed > 0:
        return "complete_with_failures", 3
    if finish_reason == "finished":
        return "complete", 0
    return "failed", 1
