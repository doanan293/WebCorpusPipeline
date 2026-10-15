from __future__ import annotations

from http import HTTPStatus

# Only an account out of credit is "quota"; per-file limits ("page limit") are not.
_QUOTA_PHRASES = ("credit", "quota", "insufficient balance", "payment required")


def classify_cloud_error(status_code: int | None, payload: object) -> str:
    message = str(payload).lower()
    account_refused = status_code in (402, 403)
    if (
        status_code == HTTPStatus.TOO_MANY_REQUESTS
        or "rate limit" in message
        or "too many requests" in message
    ):
        return "rate_limit"
    if (
        isinstance(payload, TimeoutError)
        or "timed out" in message
        or "timeout" in message
        or (status_code is not None and status_code >= HTTPStatus.INTERNAL_SERVER_ERROR)
        or status_code == HTTPStatus.REQUEST_TIMEOUT
    ):
        return "transient"
    if account_refused and any(phrase in message for phrase in _QUOTA_PHRASES):
        return "quota"
    if account_refused and "storage" in message:
        return "storage_limit"
    if status_code in (400, 422) or (account_refused and "limit" in message):
        return "invalid"
    if status_code in (401, 403):
        return "auth"
    return "unknown"
