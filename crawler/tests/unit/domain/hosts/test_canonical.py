from __future__ import annotations

from crawler.domain.hosts.canonical import deduplicate_content, record_browser_error
from crawler.domain.hosts.models import ProbeResult


def test_browser_error_marks_only_javascript_shells() -> None:
    shell = ProbeResult("b.hust.edu.vn", False, "javascript_shell")
    public = ProbeResult("a.hust.edu.vn", True, "public_response")

    marked = record_browser_error([shell, public], "RuntimeError: no browser")

    assert marked[0].error == "browser_error: RuntimeError: no browser"
    assert marked[1].error is None


def test_identical_content_keeps_the_shortest_hostname() -> None:
    results = [
        ProbeResult("www.a.hust.edu.vn", True, "public_response", content_digest="x"),
        ProbeResult("a.hust.edu.vn", True, "public_response", content_digest="x"),
    ]
    assert [r.hostname for r in deduplicate_content(results) if r.active] == ["a.hust.edu.vn"]
