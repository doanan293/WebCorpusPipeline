from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawler.application.hosts.ports import AuditReports, BrowserProber, HostProber
from crawler.domain.hosts.canonical import (
    apply_browser_attempts,
    canonicalize_redirects,
    deduplicate_content,
    record_browser_error,
)
from crawler.domain.hosts.models import ProbeResult


def run_audit(
    reports: AuditReports,
    *,
    workers: int,
    prober: HostProber,
    browser: BrowserProber | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[ProbeResult]:
    hostnames = reports.load_candidates()
    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(prober.probe, hostname): hostname for hostname in hostnames}
        for completed, future in enumerate(as_completed(futures), 1):
            hostname = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- one failing probe must not abort the whole audit
                result = ProbeResult(
                    hostname,
                    active=False,
                    reason="probe_error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            if progress is not None and (completed % 25 == 0 or completed == len(hostnames)):
                progress(completed, len(hostnames), sum(item.active for item in results))

    if browser is not None:
        browser_candidates = sorted(
            (result for result in results if result.reason == "javascript_shell"),
            key=lambda item: item.hostname,
        )
        if browser_candidates:
            try:
                attempts = browser.probe(browser_candidates)
            except Exception as exc:  # noqa: BLE001 -- a browser that cannot start must not lose the HTTP results
                results = record_browser_error(results, f"{type(exc).__name__}: {exc}")
            else:
                results = apply_browser_attempts(results, attempts)

    results = deduplicate_content(canonicalize_redirects(results))
    results.sort(key=lambda item: item.hostname)
    all_hostnames = sorted({*hostnames, *(result.hostname for result in results)})
    reports.write(all_hostnames, [result.hostname for result in results if result.active], results)
    return results


def summarize(results: list[ProbeResult], report: str) -> dict[str, object]:
    return {
        "active": sum(result.active for result in results),
        "checked": len(results),
        "report": report,
    }
