from __future__ import annotations

import html
import http.client
import ipaddress
import re
import ssl
from collections.abc import Callable
from http import HTTPStatus
from http.client import HTTPMessage
from http.cookiejar import CookieJar
from typing import IO, Any, cast, override
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from data_pipeline.domains.domain.classify import (
    classify_response,
    content_digest,
    extract_client_redirect,
    extract_title,
    is_target_hostname,
    is_target_web_url,
)
from data_pipeline.domains.domain.models import ProbeAttempt, ProbeResult, UnsafeRedirectError
from data_pipeline.shared.infrastructure.network import NonPublicAddressError, resolve_hostname

ResolveFn = Callable[[str], object]
FetchFn = Callable[[str, float], "ProbeAttempt"]


class SafeRedirectHandler(HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        target_url = urljoin(req.full_url, newurl)
        source_hostname = (urlparse(req.full_url).hostname or "").lower()
        parsed_target = urlparse(target_url)
        target_hostname = (parsed_target.hostname or "").lower()
        if target_hostname == source_hostname and not is_target_hostname(target_hostname):
            try:
                if ipaddress.ip_address(target_hostname).is_loopback:
                    return super().redirect_request(req, fp, code, msg, headers, target_url)
            except ValueError:
                pass
        if not is_target_web_url(target_url):
            raise UnsafeRedirectError(target_url, "external_redirect")
        try:
            resolve_hostname(target_hostname)
        except OSError as exc:
            raise UnsafeRedirectError(target_url, "non_public_address") from exc
        return super().redirect_request(req, fp, code, msg, headers, target_url)


def _same_host_public_urls(
    hostname: str,
    base_url: str,
    body: bytes,
) -> list[str]:
    text = html.unescape(body.decode("utf-8", errors="ignore"))
    anchors = re.findall(
        r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)",
        text,
        re.I,
    )
    locations = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
    urls: list[str] = []
    for raw_url in [*anchors, *locations]:
        candidate = urljoin(base_url, raw_url)
        parsed = urlparse(candidate)
        if parsed.hostname != hostname or parsed.scheme not in {"http", "https"}:
            continue
        lowered_path = parsed.path.lower()
        if any(
            marker in lowered_path
            for marker in (
                "login",
                "log-in",
                "signin",
                "sign-in",
                "register",
                "sign-up",
                "signup",
                "forgot",
                "reset-password",
                "password-reset",
                "/admin/",
                "dataprivacy",
                "registry-config",
                "cgi-sys",
            )
        ):
            continue
        normalized = parsed._replace(fragment="").geturl()
        if normalized.rstrip("/") == base_url.rstrip("/") or normalized in urls:
            continue
        urls.append(normalized)
    return urls[:5]


def _probe_public_subpage(
    hostname: str,
    root_url: str,
    root_attempt: ProbeAttempt,
    *,
    timeout: float,
    fetch: FetchFn,
    include_root_links: bool = True,
) -> ProbeAttempt | None:
    root_candidates = (
        _same_host_public_urls(hostname, root_url, root_attempt.body) if include_root_links else []
    )
    sitemap_url = urljoin(root_url, "/sitemap.xml")
    sitemap = fetch(sitemap_url, timeout)
    if sitemap.status is not None and HTTPStatus.OK <= sitemap.status < HTTPStatus.MULTIPLE_CHOICES:
        sitemap_candidates = _same_host_public_urls(
            hostname,
            sitemap.final_url or sitemap_url,
            sitemap.body,
        )
    else:
        sitemap_candidates: list[str] = []
    for candidate in [*root_candidates[:5], *sitemap_candidates[:5]]:
        attempt = fetch(candidate, timeout)
        if attempt.status is None:
            continue
        active, _ = classify_response(
            attempt.status,
            attempt.body,
            attempt.content_type,
            attempt.final_url,
        )
        if active:
            return attempt
    return None


_SUBPAGE_RETRY_REASONS = frozenset(
    {
        "empty_response",
        "javascript_shell",
        "login_only",
        "not_found",
        "placeholder",
        "service_endpoint",
    }
)
_NO_ROOT_LINK_REASONS = frozenset({"javascript_shell", "placeholder", "service_endpoint"})
_DETERMINISTIC_SHELLS = frozenset({"javascript_shell", "service_endpoint"})


class _HostProbe:
    """Probes one host over https then http, remembering the last responses for the fallback."""

    def __init__(
        self, hostname: str, *, timeout: float, attempts: int, resolve: ResolveFn, fetch: FetchFn
    ) -> None:
        self.hostname = hostname
        self.timeout = timeout
        self.attempts = attempts
        self.resolve = resolve
        self.fetch = fetch
        self.last_attempt: ProbeAttempt | None = None
        self.last_scheme: str | None = None
        self.last_response: ProbeAttempt | None = None
        self.last_response_scheme: str | None = None

    def run(self) -> ProbeResult:
        dns_failure = self._check_dns()
        if dns_failure is not None:
            return dns_failure
        for scheme in ("https", "http"):
            url = f"{scheme}://{self.hostname}/"
            for attempt_number in range(self.attempts):
                result = self._attempt_once(scheme, url, attempt_number)
                if result is not None:
                    return result
        return self._fallback_result()

    def _inactive(self, reason: str, **fields: Any) -> ProbeResult:
        return ProbeResult(self.hostname, active=False, reason=reason, **fields)

    def _check_dns(self) -> ProbeResult | None:
        dns_error: OSError | None = None
        for _ in range(self.attempts):
            try:
                self.resolve(self.hostname)
                dns_error = None
                break
            except NonPublicAddressError as exc:
                return self._inactive("non_public_address", error=str(exc))
            except OSError as exc:
                dns_error = exc
        if dns_error is not None:
            return self._inactive("dns_error", error=str(dns_error))
        return None

    def _describe(
        self,
        attempt: ProbeAttempt,
        *,
        scheme: str | None,
        active: bool,
        reason: str,
        digest_hostname: str | None,
        error: str | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            hostname=self.hostname,
            active=active,
            reason=reason,
            scheme=scheme,
            status=attempt.status,
            final_url=attempt.final_url,
            content_type=attempt.content_type,
            title=extract_title(attempt.body),
            content_digest=content_digest(attempt.body, digest_hostname),
            error=error,
        )

    def _attempt_once(self, scheme: str, url: str, attempt_number: int) -> ProbeResult | None:
        attempt = self.fetch(url, self.timeout)
        self.last_attempt = attempt
        self.last_scheme = scheme
        if attempt.status is None:
            return None
        if attempt.error in {"external_redirect", "non_public_address"}:
            return self._inactive(
                attempt.error,
                scheme=scheme,
                status=attempt.status,
                final_url=attempt.final_url,
                error=attempt.error,
            )
        client_redirect = extract_client_redirect(attempt.body, attempt.final_url or url)
        if client_redirect:
            outcome = self._follow_client_redirect(scheme, attempt, client_redirect)
            if not isinstance(outcome, ProbeAttempt):
                return outcome
            attempt = outcome
        self.last_response = attempt
        self.last_response_scheme = scheme
        active, reason = classify_response(
            cast(int, attempt.status), attempt.body, attempt.content_type, attempt.final_url
        )
        digest_hostname = urlparse(attempt.final_url or url).hostname
        if active:
            return self._describe(
                attempt, scheme=scheme, active=True, reason=reason, digest_hostname=digest_hostname
            )
        deterministic_shell = reason in _DETERMINISTIC_SHELLS
        if (
            attempt_number == self.attempts - 1 or deterministic_shell
        ) and reason in _SUBPAGE_RETRY_REASONS:
            public_page = _probe_public_subpage(
                self.hostname,
                url,
                attempt,
                timeout=self.timeout,
                fetch=self.fetch,
                include_root_links=reason not in _NO_ROOT_LINK_REASONS,
            )
            if public_page is not None:
                return self._describe(
                    public_page,
                    scheme=scheme,
                    active=True,
                    reason="public_response",
                    digest_hostname=urlparse(public_page.final_url or url).hostname,
                )
        if deterministic_shell:
            return self._describe(
                attempt,
                scheme=scheme,
                active=False,
                reason=reason,
                digest_hostname=digest_hostname,
                error=attempt.error,
            )
        return None

    def _follow_client_redirect(
        self, scheme: str, attempt: ProbeAttempt, redirect_url: str
    ) -> ProbeResult | ProbeAttempt | None:
        """Follow a meta/JS redirect: a final ProbeResult, the redirected attempt, or None."""
        common: dict[str, Any] = {
            "scheme": scheme,
            "status": attempt.status,
            "final_url": redirect_url,
        }
        if not is_target_web_url(redirect_url):
            return self._inactive(
                "external_redirect",
                content_type=attempt.content_type,
                title=extract_title(attempt.body),
                **common,
            )
        try:
            self.resolve(urlparse(redirect_url).hostname or "")
        except NonPublicAddressError as exc:
            return self._inactive("non_public_address", error=str(exc), **common)
        except OSError as exc:
            return self._inactive("network_error", error=f"{type(exc).__name__}: {exc}", **common)
        redirected = self.fetch(redirect_url, self.timeout)
        self.last_attempt = redirected
        if redirected.status is None:
            return None
        self.last_response = redirected
        self.last_response_scheme = scheme
        return redirected

    def _fallback_result(self) -> ProbeResult:
        if self.last_attempt is None:
            return ProbeResult(
                self.hostname, active=False, reason="network_error", error="no attempts"
            )
        response = self.last_response
        if response is None:
            return self._inactive(
                "network_error", scheme=self.last_scheme, error=self.last_attempt.error
            )
        _, reason = classify_response(
            cast(int, response.status), response.body, response.content_type, response.final_url
        )
        return self._describe(
            response,
            scheme=self.last_response_scheme,
            active=False,
            reason=reason,
            digest_hostname=urlparse(response.final_url or "").hostname or self.hostname,
            error=response.error,
        )


def probe_host(
    hostname: str,
    *,
    timeout: float,
    attempts: int,
    resolve: ResolveFn,
    fetch: FetchFn,
) -> ProbeResult:
    return _HostProbe(
        hostname, timeout=timeout, attempts=attempts, resolve=resolve, fetch=fetch
    ).run()


def fetch_url(url: str, timeout: float) -> ProbeAttempt:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        HTTPCookieProcessor(CookieJar()),
        SafeRedirectHandler(),
    )
    request = Request(  # noqa: S310 -- URLs are built from audited hostnames with an https scheme
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140 Safari/537.36"
            ),
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(256 * 1024)
            return ProbeAttempt(
                status=response.status,
                final_url=response.geturl(),
                content_type=response.headers.get_content_type(),
                body=body,
            )
    except UnsafeRedirectError as exc:
        return ProbeAttempt(
            status=302,
            final_url=exc.target_url,
            error=exc.reason,
        )
    except HTTPError as exc:
        try:
            body = exc.read(256 * 1024)
        except OSError:
            body = b""
        return ProbeAttempt(
            status=exc.code,
            final_url=exc.url,
            content_type=exc.headers.get_content_type() if exc.headers else None,
            body=body,
            error=str(exc),
        )
    except (OSError, TimeoutError, URLError, http.client.HTTPException) as exc:
        return ProbeAttempt(error=f"{type(exc).__name__}: {exc}")


class HttpHostProber:
    """The ``HostProber`` port: DNS, then https and http, with sub-page and redirect fallbacks."""

    def __init__(self, *, timeout: float, attempts: int) -> None:
        self.timeout = timeout
        self.attempts = attempts

    def probe(self, hostname: str) -> ProbeResult:
        return probe_host(
            hostname,
            timeout=self.timeout,
            attempts=self.attempts,
            resolve=resolve_hostname,
            fetch=fetch_url,
        )
