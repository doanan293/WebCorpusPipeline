from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from data_pipeline.domains.infrastructure.http_probe import (
    ProbeAttempt,
    ProbeResult,
    classify_response,
    content_digest,
    extract_title,
    fetch_url,
    is_target_hostname,
    probe_host,
)
from data_pipeline.domains.infrastructure.report_files import (
    load_candidate_hostnames,
    render_active_domains,
    render_report,
)
from data_pipeline.shared.infrastructure.files import atomic_write_text
from data_pipeline.shared.infrastructure.network import resolve_hostname

_MIN_DUPLICATE_GROUP = 2


def run_audit(
    all_path: Path,
    active_path: Path,
    report_path: Path,
    *,
    workers: int,
    probe: Callable[[str], ProbeResult],
    browser_probe: Callable[[list[ProbeResult]], dict[str, ProbeAttempt]] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[ProbeResult]:
    hostnames = load_candidate_hostnames(all_path)
    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(probe, hostname): hostname for hostname in hostnames}
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

    if browser_probe is not None:
        browser_candidates = sorted(
            (result for result in results if result.reason == "javascript_shell"),
            key=lambda item: item.hostname,
        )
        if browser_candidates:
            try:
                attempts = browser_probe(browser_candidates)
            except Exception as exc:  # noqa: BLE001 -- a browser that cannot start must not lose the HTTP results
                results = _record_browser_error(results, f"{type(exc).__name__}: {exc}")
            else:
                results = _apply_browser_attempts(results, attempts)

    results = _deduplicate_content(_canonicalize_redirects(results))
    results.sort(key=lambda item: item.hostname)
    all_hostnames = sorted({*hostnames, *(result.hostname for result in results)})
    atomic_write_text(all_path, "".join(f"{hostname}\n" for hostname in all_hostnames))
    atomic_write_text(
        active_path,
        render_active_domains(
            [result.hostname for result in results if result.active],
        ),
    )
    atomic_write_text(report_path, render_report(results))
    return results


def _record_browser_error(results: list[ProbeResult], error: str) -> list[ProbeResult]:
    """Keep each JavaScript shell's HTTP result, noting why the browser could not check it."""
    return [
        replace(result, error=f"browser_error: {error}")
        if result.reason == "javascript_shell"
        else result
        for result in results
    ]


def _canonicalize_redirects(results: list[ProbeResult]) -> list[ProbeResult]:
    by_hostname = {result.hostname: result for result in results}
    redirected: list[tuple[str, str, ProbeResult]] = []
    for result in results:
        if not result.active or not result.final_url:
            continue
        final_hostname = (urlparse(result.final_url).hostname or "").lower().rstrip(".")
        if not is_target_hostname(final_hostname):
            by_hostname[result.hostname] = replace(
                result,
                active=False,
                reason="external_redirect",
            )
        elif final_hostname != result.hostname:
            redirected.append((result.hostname, final_hostname, result))

    for source, target, evidence in redirected:
        target_result = by_hostname.get(target)
        if target_result is None or not target_result.active:
            by_hostname[target] = ProbeResult(
                hostname=target,
                active=True,
                reason="public_response",
                scheme=urlparse(evidence.final_url or "").scheme or evidence.scheme,
                status=evidence.status,
                final_url=evidence.final_url,
                content_type=evidence.content_type,
                title=evidence.title,
                content_digest=evidence.content_digest,
                browser_rendered=evidence.browser_rendered,
                checked_at=evidence.checked_at,
            )
        by_hostname[source] = replace(
            evidence,
            active=False,
            reason="redirect_duplicate",
        )
    return list(by_hostname.values())


def _deduplicate_content(results: list[ProbeResult]) -> list[ProbeResult]:
    by_digest: dict[str, list[ProbeResult]] = {}
    for result in results:
        if result.active and result.content_digest:
            by_digest.setdefault(result.content_digest, []).append(result)

    replacements: dict[str, ProbeResult] = {}
    for matches in by_digest.values():
        if len(matches) < _MIN_DUPLICATE_GROUP:
            continue
        canonical = min(matches, key=lambda item: (len(item.hostname), item.hostname))
        for result in matches:
            if result.hostname != canonical.hostname:
                replacements[result.hostname] = replace(
                    result,
                    active=False,
                    reason="content_duplicate",
                )
    return [replacements.get(result.hostname, result) for result in results]


def _apply_browser_attempts(
    results: list[ProbeResult],
    attempts: dict[str, ProbeAttempt],
) -> list[ProbeResult]:
    replacements: dict[str, ProbeResult] = {}
    for result in results:
        attempt = attempts.get(result.hostname)
        if result.reason != "javascript_shell" or attempt is None:
            continue
        if attempt.status is None:
            replacements[result.hostname] = replace(result, error=attempt.error)
            continue
        active, reason = classify_response(
            attempt.status,
            attempt.body,
            attempt.content_type,
            attempt.final_url,
        )
        replacements[result.hostname] = replace(
            result,
            active=active,
            reason=reason,
            scheme=urlparse(attempt.final_url or "").scheme or result.scheme,
            status=attempt.status,
            final_url=attempt.final_url or result.final_url,
            content_type=attempt.content_type,
            title=extract_title(attempt.body),
            content_digest=content_digest(
                attempt.body,
                urlparse(attempt.final_url or "").hostname or result.hostname,
            ),
            browser_rendered=True,
            error=attempt.error,
        )
    return [replacements.get(result.hostname, result) for result in results]


@dataclass(frozen=True, slots=True)
class AuditOptions:
    workers: int = 32
    timeout: float = 8.0
    retries: int = 2
    browser: bool = True
    browser_workers: int = 6
    browser_timeout: float = 15.0


def audit_domains(
    all_domains: Path,
    active_domains: Path,
    report: Path,
    options: AuditOptions | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, object]:
    """Probe every candidate host, rewrite the active list and report, and summarise."""
    options = options or AuditOptions()
    browser_probe = None
    if options.browser:
        # Imported only when needed, so --no-browser works without the 'crawl' extra.
        from data_pipeline.domains.infrastructure.browser_probe import browser_probe_results  # noqa: PLC0415

        browser_probe = partial(
            browser_probe_results,
            timeout=options.browser_timeout,
            workers=options.browser_workers,
        )
    results = run_audit(
        all_domains,
        active_domains,
        report,
        workers=options.workers,
        probe=partial(
            probe_host,
            timeout=options.timeout,
            attempts=options.retries + 1,
            resolve=resolve_hostname,
            fetch=fetch_url,
        ),
        browser_probe=browser_probe,
        progress=progress,
    )
    return {
        "active": sum(result.active for result in results),
        "checked": len(results),
        "report": str(report),
    }
