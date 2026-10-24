from __future__ import annotations

from crawler.domain.downloader.formats import payload_format


def test_payload_format() -> None:
    assert payload_format("https://x/a", "text/html; charset=utf-8") == "html"
    assert payload_format("https://x/a", "application/xhtml+xml") == "html"
    assert payload_format("https://x/a.pdf", "application/octet-stream") == "other"
    assert payload_format("https://x/dl?id=1", "application/pdf") == "other"
    assert payload_format("https://x/a.jpg", "image/jpeg") is None
    assert payload_format("https://x/app.js", "text/javascript") is None
