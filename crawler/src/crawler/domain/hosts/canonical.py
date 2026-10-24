from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from urllib.parse import urlparse

from crawler.domain.hosts.classify import (
    classify_response,
    content_digest,
    extract_title,
    is_target_hostname,
)
from crawler.domain.hosts.models import ProbeAttempt, ProbeResult

_MIN_DUPLICATE_GROUP = 2


def candidate_hostnames_in_scope(hostnames: Iterable[str], source: str) -> tuple[str, ...]:
    """The candidates, sorted; one outside ``hust.edu.vn`` is an error naming ``source``."""
    hostnames = set(hostnames)
    outside = sorted(
        host for host in hostnames if host != "hust.edu.vn" and not host.endswith(".hust.edu.vn")
    )
    if outside:
        msg = f"{source}: hostname outside HUST scope: {', '.join(outside)}"
        raise ValueError(msg)
    return tuple(sorted(hostnames))


def record_browser_error(results: list[ProbeResult], error: str) -> list[ProbeResult]:
    """Keep each JavaScript shell's HTTP result, noting why the browser could not check it."""
    return [
        replace(result, error=f"browser_error: {error}")
        if result.reason == "javascript_shell"
        else result
        for result in results
    ]


def canonicalize_redirects(results: list[ProbeResult]) -> list[ProbeResult]:
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


def deduplicate_content(results: list[ProbeResult]) -> list[ProbeResult]:
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


def apply_browser_attempts(
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
