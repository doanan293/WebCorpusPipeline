from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import re
import socket  # noqa: F401
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookiejar import CookieJar
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

from data_pipeline.core.policies.network import NonPublicAddressError, resolve_hostname

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


class UnsafeRedirectError(OSError):
    def __init__(self, target_url: str, reason: str) -> None:
        super().__init__(f"{reason}: {target_url}")
        self.target_url = target_url
        self.reason = reason


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
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


def classify_response(
    status: int,
    body: bytes,
    content_type: str | None = None,
    final_url: str | None = None,
) -> tuple[bool, str]:
    active, reason = classify_http_status(status)
    normalized = body.decode("utf-8", errors="ignore").lower()
    normalized_type = (content_type or "").lower()
    login_markers = ("password", "sign in", "log in", "login", "đăng nhập")
    if status in {401, 403}:
        if "<html" in normalized and any(marker in normalized for marker in login_markers):
            return False, "login_only"
        return False, "access_denied"
    if not active:
        return active, reason
    if HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES and not body.strip():
        return False, "empty_response"
    machine_readable = (
        "json" in normalized_type
        or "xml" in normalized_type
        or normalized.lstrip().startswith(("{", "[", "<?xml"))
    )
    denial_markers = ("access denied", "forbidden", "unauthorized")
    if machine_readable and any(marker in normalized for marker in denial_markers):
        return False, "access_denied"
    if machine_readable:
        return False, "service_endpoint"
    html_denial_markers = (
        "<title>access denied</title>",
        "<title>forbidden</title>",
        "<h1>access denied</h1>",
        "<h1>forbidden</h1>",
    )
    if any(marker in normalized for marker in html_denial_markers):
        return False, "access_denied"
    placeholder_markers = (
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
    if any(marker in normalized for marker in placeholder_markers):
        return False, "placeholder"
    service_markers = (
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
    if any(marker in normalized for marker in service_markers):
        return False, "service_endpoint"
    final_hostname = (urlparse(final_url).hostname or "").lower() if final_url else ""
    non_production_prefix = final_hostname.removesuffix(".hust.edu.vn")
    if re.search(
        r"(?:^|[-.])(?:dev|staging|demo|test)(?:[-.]|$)",
        non_production_prefix,
    ):
        return False, "non_production"
    login_titles = (
        "<title>login",
        "<title>log in",
        "<title>sign in",
        "<title>đăng nhập",
    )
    password_input = re.search(
        r"<input\b[^>]*\btype\s*=\s*['\"]?password\b",
        normalized,
    )
    login_path = urlparse(final_url).path.lower() if final_url else ""
    login_url = re.search(
        r"(?:^|/)(?:account/)?"
        r"(?:login|log-in|signin|sign-in|dang-nhap|weblogin|changepass)"
        r"(?:[._/-]|$)",
        login_path,
    )
    registration_url = re.search(r"(?:^|/)(?:register|signup|sign-up)(?:[._/-]|$)", login_path)
    visible_text = _visible_text(body)
    short_account_form = (
        "<form" in normalized
        and len(visible_text) < _THIN_PAGE_CHARS
        and any(
            marker in visible_text
            for marker in (
                "dang nhap",
                "mat khau",
                "mật khẩu",
                "password",
                "sign in",
                "log in",
                "đăng nhập",
            )
        )
    )
    if (
        login_url
        or (registration_url and "<form" in normalized)
        or (password_input and any(marker in normalized for marker in login_titles))
        or short_account_form
    ):
        return False, "login_only"
    html_response = "html" in normalized_type or bool(
        re.search(r"<(?:html|head|body|title|main|div|script)\b", normalized)
    )
    shell_text = visible_text.strip(" .").casefold()
    if html_response and (
        not visible_text
        or shell_text
        in {
            "loading",
            "please enable javascript",
            "javascript is required",
            "you need to enable javascript to run this app",
        }
    ):
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
    return bool(
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
    fetch,
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
        sitemap_candidates = []
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


def probe_host(
    hostname: str,
    *,
    timeout: float,
    attempts: int,
    resolve,
    fetch,
) -> ProbeResult:
    dns_error: OSError | None = None
    for _ in range(attempts):
        try:
            resolve(hostname)
            dns_error = None
            break
        except NonPublicAddressError as exc:
            return ProbeResult(
                hostname,
                active=False,
                reason="non_public_address",
                error=str(exc),
            )
        except OSError as exc:
            dns_error = exc
    if dns_error is not None:
        return ProbeResult(hostname, active=False, reason="dns_error", error=str(dns_error))

    last_attempt: ProbeAttempt | None = None
    last_scheme: str | None = None
    last_response: ProbeAttempt | None = None
    last_response_scheme: str | None = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{hostname}/"
        for attempt_number in range(attempts):
            attempt = fetch(url, timeout)
            last_attempt = attempt
            last_scheme = scheme
            if attempt.status is None:
                continue
            if attempt.error in {"external_redirect", "non_public_address"}:
                return ProbeResult(
                    hostname=hostname,
                    active=False,
                    reason=attempt.error,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    error=attempt.error,
                )
            client_redirect = _extract_client_redirect(
                attempt.body,
                attempt.final_url or url,
            )
            if client_redirect:
                target_hostname = urlparse(client_redirect).hostname
                if not is_target_web_url(client_redirect):
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="external_redirect",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        content_type=attempt.content_type,
                        title=extract_title(attempt.body),
                    )
                try:
                    resolve(target_hostname)
                except NonPublicAddressError as exc:
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="non_public_address",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        error=str(exc),
                    )
                except OSError as exc:
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="network_error",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                redirected_attempt = fetch(client_redirect, timeout)
                last_attempt = redirected_attempt
                if redirected_attempt.status is None:
                    continue
                attempt = redirected_attempt
                last_response = attempt
                last_response_scheme = scheme
            last_response = attempt
            last_response_scheme = scheme
            active, reason = classify_response(
                attempt.status,
                attempt.body,
                attempt.content_type,
                attempt.final_url,
            )
            if active:
                return ProbeResult(
                    hostname=hostname,
                    active=True,
                    reason=reason,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    content_type=attempt.content_type,
                    title=extract_title(attempt.body),
                    content_digest=content_digest(
                        attempt.body,
                        urlparse(attempt.final_url or url).hostname,
                    ),
                )
            deterministic_shell = reason in {"javascript_shell", "service_endpoint"}
            if (attempt_number == attempts - 1 or deterministic_shell) and reason in {
                "empty_response",
                "javascript_shell",
                "login_only",
                "not_found",
                "placeholder",
                "service_endpoint",
            }:
                public_page = _probe_public_subpage(
                    hostname,
                    url,
                    attempt,
                    timeout=timeout,
                    fetch=fetch,
                    include_root_links=reason
                    not in {"javascript_shell", "placeholder", "service_endpoint"},
                )
                if public_page is not None:
                    return ProbeResult(
                        hostname=hostname,
                        active=True,
                        reason="public_response",
                        scheme=scheme,
                        status=public_page.status,
                        final_url=public_page.final_url,
                        content_type=public_page.content_type,
                        title=extract_title(public_page.body),
                        content_digest=content_digest(
                            public_page.body,
                            urlparse(public_page.final_url or url).hostname,
                        ),
                    )
            if deterministic_shell:
                return ProbeResult(
                    hostname=hostname,
                    active=False,
                    reason=reason,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    content_type=attempt.content_type,
                    title=extract_title(attempt.body),
                    content_digest=content_digest(
                        attempt.body,
                        urlparse(attempt.final_url or url).hostname,
                    ),
                    error=attempt.error,
                )

    if last_attempt is None:
        return ProbeResult(hostname, active=False, reason="network_error", error="no attempts")
    if last_response is not None:
        _, reason = classify_response(
            last_response.status,
            last_response.body,
            last_response.content_type,
            last_response.final_url,
        )
        return ProbeResult(
            hostname=hostname,
            active=False,
            reason=reason,
            scheme=last_response_scheme,
            status=last_response.status,
            final_url=last_response.final_url,
            content_type=last_response.content_type,
            title=extract_title(last_response.body),
            content_digest=content_digest(
                last_response.body,
                urlparse(last_response.final_url or "").hostname or hostname,
            ),
            error=last_response.error,
        )
    return ProbeResult(
        hostname,
        active=False,
        reason="network_error",
        scheme=last_scheme,
        error=last_attempt.error,
    )


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
    request = Request(
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
            final_url=exc.geturl(),
            content_type=exc.headers.get_content_type() if exc.headers else None,
            body=body,
            error=str(exc),
        )
    except (OSError, TimeoutError, URLError, http.client.HTTPException) as exc:
        return ProbeAttempt(error=f"{type(exc).__name__}: {exc}")
