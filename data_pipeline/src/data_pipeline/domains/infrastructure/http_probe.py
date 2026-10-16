from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import re
import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

from data_pipeline.shared.infrastructure.network import NonPublicAddressError, resolve_hostname

_THIN_PAGE_CHARS = 500


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    hostname: str
    active: bool
    reason: str
    scheme: str | None = None
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    content_digest: str | None = None
    browser_rendered: bool = False
    error: str | None = None
    checked_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class ProbeAttempt:
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    body: bytes = b""
    error: str | None = None


ResolveFn = Callable[[str], object]
FetchFn = Callable[[str, float], "ProbeAttempt"]


class UnsafeRedirectError(OSError):
    def __init__(self, target_url: str, reason: str) -> None:
        super().__init__(f"{reason}: {target_url}")
        self.target_url = target_url
        self.reason = reason


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


def _visible_text(body: bytes, hostname: str | None = None) -> str:
    text = body.decode("utf-8", errors="ignore")
    if hostname:
        text = re.sub(re.escape(hostname), "{host}", text, flags=re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(
        r"<(?P<hidden>head|script|style|template|noscript)\b[^>]*>.*?"
        r"</(?P=hidden)\s*>",
        " ",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"<title\b[^>]*>.*?</title\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split()).casefold()


def classify_http_status(status: int) -> tuple[bool, str]:
    if HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
        return True, "public_response"
    if HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.BAD_REQUEST:
        return False, "redirect_error"
    if status in {401, 403}:
        return False, "access_denied"
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return False, "rate_limited"
    if status == HTTPStatus.NOT_FOUND:
        return False, "not_found"
    if status == HTTPStatus.GONE:
        return False, "gone"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return False, "server_error"
    return False, "client_error"


_LOGIN_MARKERS = ("password", "sign in", "log in", "login", "đăng nhập")
_DENIAL_MARKERS = ("access denied", "forbidden", "unauthorized")
_HTML_DENIAL_MARKERS = (
    "<title>access denied</title>",
    "<title>forbidden</title>",
    "<h1>access denied</h1>",
    "<h1>forbidden</h1>",
)
_PLACEHOLDER_MARKERS = (
    "welcome to nginx",
    "apache2 ubuntu default page",
    "website under construction",
    "website is under construction",
    "domain for sale",
    "this domain is parked",
    "http server test page powered by centos-webpanel.com",
    "<title>account suspended</title>",
    "openlitespeed is functioning normally",
    "trang thông tin này chưa kích hoạt",
    "<title>plesk ",
    "<title>vinahost plesk",
    "<title>web server's default page</title>",
    "<title>404</title>",
    "powered_by_cpanel.svg",
    "cpanel, inc.",
    "/cgi-sys/defaultwebpage.cgi",
    "website hiện không hoạt động",
    "webserver is functioning normally",
    "add anything here or just remove it",
    "welcome to wordpress. this is your first post.",
    "<title>apache tomcat",
)
_SERVICE_MARKERS = (
    "<title>cfssl</title>",
    "esxuiapp",
    "<title>bkoffice</title>",
    "email|pass|refresh_token|client_id",
    "<title>d-office-hust</title>",
    "<title>bksign | hệ thống ký số</title>",
    "<title>ediploma</title>",
    "<title>hustack</title>",
    "<title>tsa-exam",
)
_LOGIN_TITLES = ("<title>login", "<title>log in", "<title>sign in", "<title>đăng nhập")
_ACCOUNT_FORM_MARKERS = (
    "dang nhap",
    "mat khau",
    "mật khẩu",
    "password",
    "sign in",
    "log in",
    "đăng nhập",
)
_SHELL_TEXTS = frozenset(
    {
        "loading",
        "please enable javascript",
        "javascript is required",
        "you need to enable javascript to run this app",
    }
)
_NON_PRODUCTION_RE = re.compile(r"(?:^|[-.])(?:dev|staging|demo|test)(?:[-.]|$)")
_PASSWORD_INPUT_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*['\"]?password\b")
_LOGIN_URL_RE = re.compile(
    r"(?:^|/)(?:account/)?"
    r"(?:login|log-in|signin|sign-in|dang-nhap|weblogin|changepass)"
    r"(?:[._/-]|$)"
)
_REGISTRATION_URL_RE = re.compile(r"(?:^|/)(?:register|signup|sign-up)(?:[._/-]|$)")
_HTML_TAG_RE = re.compile(r"<(?:html|head|body|title|main|div|script)\b")


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _machine_readable_rejection(normalized: str, content_type: str) -> str | None:
    machine_readable = (
        "json" in content_type
        or "xml" in content_type
        or normalized.lstrip().startswith(("{", "[", "<?xml"))
    )
    if not machine_readable:
        return None
    return "access_denied" if _contains_any(normalized, _DENIAL_MARKERS) else "service_endpoint"


def _marker_rejection(normalized: str) -> str | None:
    if _contains_any(normalized, _HTML_DENIAL_MARKERS):
        return "access_denied"
    if _contains_any(normalized, _PLACEHOLDER_MARKERS):
        return "placeholder"
    if _contains_any(normalized, _SERVICE_MARKERS):
        return "service_endpoint"
    return None


def _is_non_production(final_url: str | None) -> bool:
    final_hostname = (urlparse(final_url).hostname or "").lower() if final_url else ""
    return bool(_NON_PRODUCTION_RE.search(final_hostname.removesuffix(".hust.edu.vn")))


def _is_login_page(normalized: str, visible_text: str, final_url: str | None) -> bool:
    login_path = urlparse(final_url).path.lower() if final_url else ""
    has_form = "<form" in normalized
    short_account_form = (
        has_form
        and len(visible_text) < _THIN_PAGE_CHARS
        and _contains_any(visible_text, _ACCOUNT_FORM_MARKERS)
    )
    return bool(
        _LOGIN_URL_RE.search(login_path)
        or (_REGISTRATION_URL_RE.search(login_path) and has_form)
        or (_PASSWORD_INPUT_RE.search(normalized) and _contains_any(normalized, _LOGIN_TITLES))
        or short_account_form
    )


def _is_javascript_shell(normalized: str, content_type: str, visible_text: str) -> bool:
    html_response = "html" in content_type or bool(_HTML_TAG_RE.search(normalized))
    shell_text = visible_text.strip(" .").casefold()
    return html_response and (not visible_text or shell_text in _SHELL_TEXTS)


def classify_response(
    status: int,
    body: bytes,
    content_type: str | None = None,
    final_url: str | None = None,
) -> tuple[bool, str]:
    active, reason = classify_http_status(status)
    normalized = body.decode("utf-8", errors="ignore").lower()
    normalized_type = (content_type or "").lower()
    if status in {401, 403}:
        if "<html" in normalized and _contains_any(normalized, _LOGIN_MARKERS):
            return False, "login_only"
        return False, "access_denied"
    if not active:
        return active, reason
    if HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES and not body.strip():
        return False, "empty_response"
    rejection = _machine_readable_rejection(normalized, normalized_type) or _marker_rejection(
        normalized
    )
    if rejection is None and _is_non_production(final_url):
        rejection = "non_production"
    if rejection is not None:
        return False, rejection
    visible_text = _visible_text(body)
    if _is_login_page(normalized, visible_text, final_url):
        return False, "login_only"
    if _is_javascript_shell(normalized, normalized_type, visible_text):
        return False, "javascript_shell"
    return active, reason


def is_target_hostname(hostname: str | None) -> bool:
    return bool(hostname and (hostname == "hust.edu.vn" or hostname.endswith(".hust.edu.vn")))


def is_target_web_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and is_target_hostname((parsed.hostname or "").lower())
        and (port is None or port == (80 if parsed.scheme == "http" else 443))
    )


def _extract_client_redirect(body: bytes, base_url: str) -> str | None:
    text = body.decode("utf-8", errors="ignore")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        if not re.search(r"http-equiv\s*=\s*['\"]?refresh", tag, re.I):
            continue
        content = re.search(r"content\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        content_value = content.group(2) if content else None
        if content_value is None:
            unquoted = re.search(r"content\s*=\s*([^\s>]+)", tag, re.I)
            content_value = unquoted.group(1) if unquoted else None
        if content_value is None:
            continue
        target = re.search(r"url\s*=\s*['\"]?([^'\";\s]+)", content_value, re.I)
        if target:
            return urljoin(base_url, html.unescape(target.group(1)))
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.I | re.S):
        statement = script.strip()
        javascript = re.fullmatch(
            r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]\s*;?",
            statement,
            re.I,
        )
        replace_call = re.fullmatch(
            r"(?:window\.)?location\.replace\(\s*['\"]([^'\"]+)['\"]\s*\)\s*;?",
            statement,
            re.I,
        )
        redirect = javascript or replace_call
        if redirect:
            return urljoin(base_url, redirect.group(1).strip())
    return None


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


def extract_title(body: bytes) -> str | None:
    match = re.search(rb"<title\b[^>]*>(.*?)</title>", body, re.I | re.S)
    if not match:
        return None
    raw_title = match.group(1).decode("utf-8", errors="replace")
    title = html.unescape(re.sub(r"\s+", " ", raw_title)).strip()
    return title or None


def content_digest(body: bytes, hostname: str | None = None) -> str | None:
    if not body:
        return None
    return hashlib.sha256(_visible_text(body, hostname).encode()).hexdigest()


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
        client_redirect = _extract_client_redirect(attempt.body, attempt.final_url or url)
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
