from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable
from http import HTTPStatus
from urllib.parse import urljoin, urlparse

_THIN_PAGE_CHARS = 500


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


def extract_client_redirect(body: bytes, base_url: str) -> str | None:
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
