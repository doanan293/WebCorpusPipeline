from __future__ import annotations

import argparse
import json
import socket  # noqa: F401
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from data_pipeline.core.policies.network import resolve_hostname
from data_pipeline.domains.browser import browser_probe_results
from data_pipeline.domains.probe import (
    ProbeAttempt,
    ProbeResult,
    classify_response,
    content_digest,
    extract_title,
    fetch_url,
    is_target_hostname,
    probe_host,
)
from data_pipeline.domains.report import (
    atomic_write_text,
    load_candidate_hostnames,
    render_active_domains,
    render_report,
)


def run_audit(
    all_path: Path,
    active_path: Path,
    report_path: Path,
    *,
    workers: int,
    probe,
    browser_probe=None,
) -> list[ProbeResult]:
    hostnames = load_candidate_hostnames(all_path)
    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(probe, hostname): hostname for hostname in hostnames}
        for completed, future in enumerate(as_completed(futures), 1):
            hostname = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = ProbeResult(
                    hostname,
                    False,
                    "probe_error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            if completed % 25 == 0 or completed == len(hostnames):
                active_count = sum(item.active for item in results)
                print(
                    f"checked {completed}/{len(hostnames)}; active {active_count}",
                    flush=True,
                )

    if browser_probe is not None:
        browser_candidates = sorted(
            (result for result in results if result.reason == "javascript_shell"),
            key=lambda item: item.hostname,
        )
        if browser_candidates:
            results = _apply_browser_attempts(
                results,
                browser_probe(browser_candidates),
            )

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
        if len(matches) < 2:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hust-domain-audit")
    parser.add_argument("--all-domains", type=Path, default=Path("docs/domain_all.txt"))
    parser.add_argument("--active-domains", type=Path, default=Path("docs/domain_active.txt"))
    parser.add_argument("--report", type=Path, default=Path("docs/domain_status.jsonl"))
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--browser-workers", type=int, default=6)
    parser.add_argument("--browser-timeout", type=float, default=15.0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args(argv)
    if (
        args.workers <= 0
        or args.browser_workers <= 0
        or args.timeout <= 0
        or args.browser_timeout <= 0
        or args.retries < 0
    ):
        parser.error("workers and timeouts must be positive; retries cannot be negative")

    results = run_audit(
        args.all_domains,
        args.active_domains,
        args.report,
        workers=args.workers,
        probe=partial(
            probe_host,
            timeout=args.timeout,
            attempts=args.retries + 1,
            resolve=resolve_hostname,
            fetch=fetch_url,
        ),
        browser_probe=(
            None
            if args.no_browser
            else partial(
                browser_probe_results,
                timeout=args.browser_timeout,
                workers=args.browser_workers,
            )
        ),
    )
    print(
        json.dumps(
            {
                "active": sum(result.active for result in results),
                "checked": len(results),
                "report": str(args.report),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
