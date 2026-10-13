from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline.cli.main import main
from data_pipeline.core.paths import ProjectPaths
from data_pipeline.domains import audit as domain_audit
from data_pipeline.domains.probe import ProbeAttempt


def resolve_ok(hostname: str) -> None:
    return None


def test_audit_updates_domain_files_with_the_project_defaults(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project.domains_all.parent.mkdir(parents=True)
    project.domains_all.write_text("good.hust.edu.vn\n", encoding="utf-8")

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        return ProbeAttempt(
            status=200,
            final_url=url,
            body=b"<title>Working</title><h1>Public information</h1>",
        )

    monkeypatch.setattr(domain_audit, "fetch_url", fetch)
    monkeypatch.setattr(domain_audit, "resolve_hostname", resolve_ok)

    code = main(["domains", "audit", "--workers", "1", "--timeout", "1", "--no-browser"])

    assert code == 0
    assert project.domains_active.read_text(encoding="utf-8").split() == ["good.hust.edu.vn"]
    assert json.loads(project.domains_status.read_text(encoding="utf-8").splitlines()[0])["active"]
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["active"] == 1
    assert summary["checked"] == 1


@pytest.mark.parametrize(
    "flags",
    [["--workers", "0"], ["--timeout", "0"], ["--retries", "-1"], ["--browser-workers", "0"]],
)
def test_audit_rejects_invalid_numbers(
    flags: list[str], project: ProjectPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["domains", "audit", *flags]) == 2
    assert "must be positive" in capsys.readouterr().err


def test_audit_updates_domain_files_with_configured_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    all_path = tmp_path / "all.txt"
    all_path.write_text("a.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "active.txt"
    active_path.write_text("List active domain:\n", encoding="utf-8")
    report_path = tmp_path / "status.jsonl"
    monkeypatch.setattr(domain_audit, "resolve_hostname", resolve_ok)
    fetch_count = 0

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count <= 2:
            return ProbeAttempt(error="temporary failure")
        return ProbeAttempt(
            status=200,
            final_url=url,
            body=b"<title>Working</title><h1>Public information</h1>",
        )

    monkeypatch.setattr(domain_audit, "fetch_url", fetch)

    exit_code = main(
        [
            "domains",
            "audit",
            "--all-domains",
            str(all_path),
            "--active-domains",
            str(active_path),
            "--report",
            str(report_path),
            "--workers",
            "1",
            "--timeout",
            "1",
            "--retries",
            "2",
        ]
    )

    assert exit_code == 0
    assert fetch_count == 3
    assert active_path.read_text() == "a.hust.edu.vn\n"
    assert json.loads(report_path.read_text())["active"] is True
