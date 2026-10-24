from __future__ import annotations

import pytest

from crawler.application.hosts.audit import run_audit, summarize
from crawler.cli import domains as cli_domains
from crawler.domain.hosts.models import ProbeAttempt, ProbeResult
from tests.fakes.hosts import FakeBrowser, FakeProber, MemoryReports


def shell(hostname: str) -> ProbeResult:
    return ProbeResult(hostname, False, "javascript_shell", scheme="https", status=200)


def test_run_audit_updates_both_lists_and_writes_report() -> None:
    reports = MemoryReports("b.hust.edu.vn", "a.hust.edu.vn")

    results = run_audit(
        reports,
        workers=2,
        prober=FakeProber(
            lambda hostname: ProbeResult(
                hostname,
                hostname.startswith("a."),
                "public_response" if hostname.startswith("a.") else "dns_error",
            )
        ),
    )

    assert [result.hostname for result in results] == ["a.hust.edu.vn", "b.hust.edu.vn"]
    assert reports.all_hostnames == ["a.hust.edu.vn", "b.hust.edu.vn"]
    assert reports.active == ["a.hust.edu.vn"]
    assert len(reports.results) == 2
    assert summarize(results, "r.jsonl") == {"active": 1, "checked": 2, "report": "r.jsonl"}


def test_run_audit_promotes_only_public_browser_rendered_shells() -> None:
    reports = MemoryReports("login.hust.edu.vn", "portal.hust.edu.vn")

    def render(results: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        return {
            "login.hust.edu.vn": ProbeAttempt(
                status=200,
                final_url="https://login.hust.edu.vn/login",
                content_type="text/html",
                body=b"<title>Portal</title><form><input type='password'></form>",
            ),
            "portal.hust.edu.vn": ProbeAttempt(
                status=200,
                final_url="https://portal.hust.edu.vn/",
                content_type="text/html",
                body=b"<title>Portal</title><main><h1>Public news</h1></main>",
            ),
        }

    browser = FakeBrowser(render)
    results = run_audit(
        reports,
        workers=1,
        prober=FakeProber(
            lambda hostname: ProbeResult(
                hostname,
                False,
                "javascript_shell",
                scheme="https",
                status=200,
                final_url=f"https://{hostname}/",
                content_type="text/html",
            )
        ),
        browser=browser,
    )

    assert browser.calls == [["login.hust.edu.vn", "portal.hust.edu.vn"]]
    assert reports.active == ["portal.hust.edu.vn"]
    assert [(item.hostname, item.active, item.reason) for item in results] == [
        ("login.hust.edu.vn", False, "login_only"),
        ("portal.hust.edu.vn", True, "public_response"),
    ]
    assert results[1].browser_rendered is True


def test_run_audit_keeps_only_canonical_hust_redirect_target() -> None:
    reports = MemoryReports("alias.hust.edu.vn", "canonical.hust.edu.vn")

    results = run_audit(
        reports,
        workers=1,
        prober=FakeProber(
            lambda hostname: ProbeResult(
                hostname,
                True,
                "public_response",
                scheme="https",
                status=200,
                final_url="https://canonical.hust.edu.vn/",
                title="Canonical site",
            )
        ),
    )

    assert reports.active == ["canonical.hust.edu.vn"]
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("alias.hust.edu.vn", False, "redirect_duplicate"),
        ("canonical.hust.edu.vn", True, "public_response"),
    ]


def test_run_audit_excludes_redirect_outside_hust_scope() -> None:
    reports = MemoryReports("journal.hust.edu.vn")

    results = run_audit(
        reports,
        workers=1,
        prober=FakeProber(
            lambda hostname: ProbeResult(
                hostname,
                True,
                "public_response",
                scheme="https",
                status=200,
                final_url="https://journal.example.org/",
                title="External journal",
            )
        ),
    )

    assert reports.active == []
    assert results[0].active is False
    assert results[0].reason == "external_redirect"


def test_run_audit_keeps_shortest_hostname_for_identical_content() -> None:
    reports = MemoryReports("unit-meta.hust.edu.vn", "unit.hust.edu.vn")

    results = run_audit(
        reports,
        workers=1,
        prober=FakeProber(
            lambda hostname: ProbeResult(
                hostname,
                True,
                "public_response",
                scheme="https",
                status=200,
                final_url=f"https://{hostname}/",
                title="Same public site",
                content_digest="same-body-digest",
            )
        ),
    )

    assert reports.active == ["unit.hust.edu.vn"]
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("unit-meta.hust.edu.vn", False, "content_duplicate"),
        ("unit.hust.edu.vn", True, "public_response"),
    ]


def test_browser_launch_failure_keeps_http_results() -> None:
    reports = MemoryReports("a.hust.edu.vn", "b.hust.edu.vn")

    def broken_browser(results: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        msg = "Executable doesn't exist"
        raise RuntimeError(msg)

    def probe(hostname: str) -> ProbeResult:
        if hostname.startswith("a."):
            return ProbeResult(hostname, True, "public_response", scheme="https", status=200)
        return shell(hostname)

    results = run_audit(
        reports, workers=1, prober=FakeProber(probe), browser=FakeBrowser(broken_browser)
    )

    assert reports.active == ["a.hust.edu.vn"]
    by_host = {item.hostname: item for item in results}
    assert by_host["b.hust.edu.vn"].reason == "javascript_shell"
    assert "browser_error: RuntimeError: Executable doesn't exist" in str(
        by_host["b.hust.edu.vn"].error
    )


def test_a_crashing_probe_is_recorded_not_raised() -> None:
    reports = MemoryReports("a.hust.edu.vn")

    def explode(hostname: str) -> ProbeResult:
        msg = "boom"
        raise RuntimeError(msg)

    results = run_audit(reports, workers=1, prober=FakeProber(explode))

    assert (results[0].reason, results[0].error) == ("probe_error", "RuntimeError: boom")


def test_progress_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    cli_domains._print_progress(25, 100, 7)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "checked 25/100; active 7" in captured.err
