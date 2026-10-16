import importlib
import sys
from pathlib import Path

import pytest

from data_pipeline.cli import domains as cli_domains
from data_pipeline.domains.application.audit import AuditOptions, run_audit
from data_pipeline.domains.infrastructure.http_probe import (
    ProbeAttempt,
    ProbeResult,
)


def test_run_audit_updates_both_lists_and_writes_report(tmp_path: Path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("b.hust.edu.vn\na.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    active_path.write_text(
        "Tên nhóm: BK\nList active domain:\nold.hust.edu.vn\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=2,
        probe=lambda hostname: ProbeResult(
            hostname,
            hostname.startswith("a."),
            "public_response" if hostname.startswith("a.") else "dns_error",
        ),
    )

    assert [result.hostname for result in results] == [
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    ]
    assert all_path.read_text() == "a.hust.edu.vn\nb.hust.edu.vn\n"
    assert active_path.read_text() == "a.hust.edu.vn\n"
    assert len(report_path.read_text().splitlines()) == 2


def test_run_audit_promotes_only_public_browser_rendered_shells(tmp_path: Path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text(
        "login.hust.edu.vn\nportal.hust.edu.vn\n",
        encoding="utf-8",
    )
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"
    browser_calls: list[list[str]] = []

    def browser_probe(results: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        browser_calls.append([result.hostname for result in results])
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

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            False,
            "javascript_shell",
            scheme="https",
            status=200,
            final_url=f"https://{hostname}/",
            content_type="text/html",
        ),
        browser_probe=browser_probe,
    )

    assert browser_calls == [["login.hust.edu.vn", "portal.hust.edu.vn"]]
    assert active_path.read_text() == "portal.hust.edu.vn\n"
    assert [(item.hostname, item.active, item.reason) for item in results] == [
        ("login.hust.edu.vn", False, "login_only"),
        ("portal.hust.edu.vn", True, "public_response"),
    ]
    assert results[1].browser_rendered is True


def test_run_audit_keeps_only_canonical_hust_redirect_target(tmp_path: Path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("alias.hust.edu.vn\ncanonical.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://canonical.hust.edu.vn/",
            title="Canonical site",
        ),
    )

    assert active_path.read_text() == "canonical.hust.edu.vn\n"
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("alias.hust.edu.vn", False, "redirect_duplicate"),
        ("canonical.hust.edu.vn", True, "public_response"),
    ]


def test_run_audit_excludes_redirect_outside_hust_scope(tmp_path: Path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("journal.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://journal.example.org/",
            title="External journal",
        ),
    )

    assert active_path.read_text() == ""
    assert results[0].active is False
    assert results[0].reason == "external_redirect"


def test_run_audit_keeps_shortest_hostname_for_identical_content(tmp_path: Path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text(
        "unit-meta.hust.edu.vn\nunit.hust.edu.vn\n",
        encoding="utf-8",
    )
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url=f"https://{hostname}/",
            title="Same public site",
            content_digest="same-body-digest",
        ),
    )

    assert active_path.read_text() == "unit.hust.edu.vn\n"
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("unit-meta.hust.edu.vn", False, "content_duplicate"),
        ("unit.hust.edu.vn", True, "public_response"),
    ]


def shell(hostname: str) -> ProbeResult:
    return ProbeResult(hostname, False, "javascript_shell", scheme="https", status=200)


def test_browser_launch_failure_keeps_http_results(tmp_path: Path) -> None:
    all_path = tmp_path / "all.txt"
    all_path.write_text("a.hust.edu.vn\nb.hust.edu.vn\n", encoding="utf-8")

    def broken_browser(results: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        msg = "Executable doesn't exist"
        raise RuntimeError(msg)

    def probe(hostname: str) -> ProbeResult:
        if hostname.startswith("a."):
            return ProbeResult(hostname, True, "public_response", scheme="https", status=200)
        return shell(hostname)

    results = run_audit(
        all_path,
        tmp_path / "active.txt",
        tmp_path / "report.jsonl",
        workers=1,
        probe=probe,
        browser_probe=broken_browser,
    )

    assert (tmp_path / "active.txt").read_text() == "a.hust.edu.vn\n"
    by_host = {item.hostname: item for item in results}
    assert by_host["b.hust.edu.vn"].reason == "javascript_shell"
    assert "browser_error: RuntimeError: Executable doesn't exist" in str(
        by_host["b.hust.edu.vn"].error
    )


def test_no_browser_works_without_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("playwright", "playwright.async_api"):
        monkeypatch.setitem(sys.modules, name, None)
    for name in (
        "data_pipeline.domains.infrastructure.browser_probe",
        "data_pipeline.domains.application.audit",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    fresh = importlib.import_module("data_pipeline.domains.application.audit")
    all_path = tmp_path / "all.txt"
    all_path.write_text("a.hust.edu.vn\n", encoding="utf-8")

    def probe_host(hostname: str, **_: object) -> ProbeResult:
        return shell(hostname)

    monkeypatch.setattr(fresh, "probe_host", probe_host)

    summary = fresh.audit_domains(
        all_path, tmp_path / "active.txt", tmp_path / "report.jsonl", AuditOptions(browser=False)
    )

    assert summary["checked"] == 1


def test_progress_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    cli_domains._print_progress(25, 100, 7)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "checked 25/100; active 7" in captured.err
