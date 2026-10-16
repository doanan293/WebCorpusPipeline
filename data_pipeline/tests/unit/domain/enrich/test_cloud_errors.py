import pytest

from data_pipeline.domain.enrich.cloud_errors import classify_cloud_error


@pytest.mark.parametrize(
    ("status", "message", "kind"),
    [
        (402, "Insufficient credits", "quota"),
        (403, "Monthly quota exceeded", "quota"),
        (403, "page limit exceeded for this file", "invalid"),
        (429, "Too many requests", "rate_limit"),
        (None, "Request timed out.", "transient"),
        (504, "gateway timeout", "transient"),
        (401, "bad key", "auth"),
    ],
)
def test_classify_cloud_error(status: int | None, message: str, kind: str) -> None:
    assert classify_cloud_error(status, RuntimeError(message)) == kind


def test_timeout_is_transient() -> None:
    assert classify_cloud_error(None, TimeoutError()) == "transient"
