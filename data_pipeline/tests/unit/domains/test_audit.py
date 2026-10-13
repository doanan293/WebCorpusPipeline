from data_pipeline.domains.audit import run_audit
from data_pipeline.domains.probe import (
    ProbeAttempt,
    ProbeResult,
)


def test_run_audit_updates_both_lists_and_writes_report(tmp_path) -> None:
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


def test_run_audit_promotes_only_public_browser_rendered_shells(tmp_path) -> None:
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


def test_run_audit_keeps_only_canonical_hust_redirect_target(tmp_path) -> None:
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


def test_run_audit_excludes_redirect_outside_hust_scope(tmp_path) -> None:
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


def test_run_audit_keeps_shortest_hostname_for_identical_content(tmp_path) -> None:
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
