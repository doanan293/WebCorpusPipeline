from __future__ import annotations

from http import HTTPStatus


def classify_cloud_error(status_code: int | None, payload: object) -> str:
    message = str(payload).lower()
    if (
        status_code == HTTPStatus.TOO_MANY_REQUESTS
        or "rate limit" in message
        or "too many requests" in message
    ):
        return "rate_limit"
    if status_code in (402, 403) and any(
        word in message for word in ("credit", "quota", "limit", "balance")
    ):
        return "quota"
    if status_code in (402, 403) and "storage" in message:
        return "storage_limit"
    if status_code in (401, 403):
        return "auth"
    if status_code in (400, 422):
        return "invalid"
    if status_code is not None and status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "transient"
    return "unknown"
