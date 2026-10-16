import pytest

from data_pipeline.enrich.domain.download_rules import (
    PermanentDownloadError,
    RetryableDownloadError,
    is_strong_etag,
    looks_like_document,
    raise_for_status,
    range_start,
    retry_after_seconds,
)


def test_strong_etag_and_range_start() -> None:
    assert is_strong_etag('"abc"') and not is_strong_etag('W/"abc"') and not is_strong_etag(None)
    assert range_start("bytes 100-200/300") == 100
    assert range_start("garbage") is None


def test_retry_after_seconds_reads_seconds_and_http_dates() -> None:
    assert retry_after_seconds("120", now=0.0) == 120.0
    later = retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT", now=0.0)
    assert later is not None and later > 4_000_000_000
    assert retry_after_seconds("soon", now=0.0) is None


def test_raise_for_status_distinguishes_retryable_from_permanent() -> None:
    raise_for_status(200, {})
    with pytest.raises(RetryableDownloadError) as retry:
        raise_for_status(503, {"Retry-After": "7"})
    assert retry.value.retry_after == 7.0
    with pytest.raises(PermanentDownloadError):
        raise_for_status(404, {})


def test_looks_like_document_sniffs_bytes() -> None:
    assert looks_like_document(b"%PDF-1.4\n...", "application/pdf")
    assert not looks_like_document(b"<html><body>no</body></html>", "application/pdf")
