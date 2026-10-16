from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline.cli import context, errors

app = typer.Typer(help="Check which HUST hostnames are alive.", no_args_is_help=True)


def _print_progress(checked: int, total: int, active: int) -> None:
    print(f"checked {checked}/{total}; active {active}", file=sys.stderr, flush=True)


@app.command()
def audit(
    all_domains: Annotated[
        Path | None, typer.Option(help="Candidate hostnames (default: config/domains/all.txt).")
    ] = None,
    active_domains: Annotated[
        Path | None,
        typer.Option(help="Active hostnames to write (default: config/domains/active.txt)."),
    ] = None,
    report: Annotated[
        Path | None, typer.Option(help="Per-host report (default: config/domains/status.jsonl).")
    ] = None,
    workers: Annotated[int, typer.Option(help="Concurrent HTTP probes.")] = 32,
    browser_workers: Annotated[int, typer.Option(help="Concurrent browser probes.")] = 6,
    browser_timeout: Annotated[float, typer.Option(help="Seconds per browser probe.")] = 15.0,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Skip browser probes.")] = False,
    timeout: Annotated[float, typer.Option(help="Seconds per HTTP probe.")] = 8.0,
    retries: Annotated[int, typer.Option(help="Extra attempts per host.")] = 2,
) -> int:
    """Probe every candidate hostname and rewrite the active list."""
    if workers <= 0 or browser_workers <= 0 or timeout <= 0 or browser_timeout <= 0 or retries < 0:
        return errors.fail("workers and timeouts must be positive; retries cannot be negative")

    from data_pipeline.domains.application import audit as domain_audit
    from data_pipeline.domains.application.ports import BrowserProber
    from data_pipeline.domains.infrastructure.http_probe import HttpHostProber
    from data_pipeline.domains.infrastructure.report_files import FileAuditReports

    paths = context.current_paths()
    report_path = context.pick(report, paths.domains_status)
    browser: BrowserProber | None = None
    if not no_browser:
        # Imported only when needed, so --no-browser works without the 'crawl' extra.
        from data_pipeline.domains.infrastructure.browser_probe import PlaywrightBrowserProber

        browser = PlaywrightBrowserProber(timeout=browser_timeout, workers=browser_workers)
    results = domain_audit.run_audit(
        FileAuditReports(
            context.pick(all_domains, paths.domains_all),
            context.pick(active_domains, paths.domains_active),
            report_path,
        ),
        workers=workers,
        prober=HttpHostProber(timeout=timeout, attempts=retries + 1),
        browser=browser,
        progress=_print_progress,
    )
    summary = domain_audit.summarize(results, str(report_path))
    print(json.dumps(summary, sort_keys=True))
    return 0
