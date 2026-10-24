from __future__ import annotations

import pytest

from crawler.domain.hosts.classify import (
    classify_http_status,
    classify_response,
)
from crawler.domain.hosts.models import ProbeResult


@pytest.mark.parametrize(
    ("status", "active", "reason"),
    [
        (200, True, "public_response"),
        (204, True, "public_response"),
        (302, False, "redirect_error"),
        (401, False, "access_denied"),
        (403, False, "access_denied"),
        (429, False, "rate_limited"),
        (404, False, "not_found"),
        (410, False, "gone"),
        (500, False, "server_error"),
        (503, False, "server_error"),
    ],
)
def test_classify_http_status(status: int, active: bool, reason: str) -> None:
    assert classify_http_status(status) == (active, reason)


@pytest.mark.parametrize(
    "body",
    [
        b"<title>Welcome to nginx!</title>",
        b"<h1>Apache2 Ubuntu Default Page</h1>",
        b"<title>Website under construction</title>",
        b"<title>HTTP Server Test Page powered by CentOS-WebPanel.com</title>",
        b"<title>Account Suspended</title>",
        b"OpenLiteSpeed is functioning normally",
        "Trang thông tin này chưa kích hoạt.".encode(),
        b"<title>Plesk Obsidian 18.0.60</title>",
        b"<title>Web Server's Default Page</title>",
        b"<title>404</title>",
        (
            b"<title>403 Forbidden</title><p>Access is forbidden to the requested page:</p>"
            b'<a href="http://cpanel.com/">cPanel, Inc.</a>'
        ),
        (b'<meta http-equiv="refresh" content="0;URL=/cgi-sys/defaultwebpage.cgi"><body></body>'),
        "<title>Website hiện không hoạt động</title>".encode(),
        b"webserver is functioning normally",
        b"ADD ANYTHING HERE OR JUST REMOVE IT... Welcome to WordPress. This is your first post.",
        b"<title>Apache Tomcat/10.1.55</title>",
        b"<title>Apache Tomcat 10 (10.1.55) - Documentation Index</title><h1>Documentation</h1>",
    ],
)
def test_placeholder_page_is_not_active(body: bytes) -> None:
    assert classify_response(200, body) == (False, "placeholder")


def test_empty_success_response_is_not_an_active_website() -> None:
    assert classify_response(200, b"") == (False, "empty_response")
    assert classify_response(204, b"") == (False, "empty_response")


def test_machine_readable_success_access_denial_is_not_active() -> None:
    body = b'{"error":"access denied","status":"unauthorized"}'

    assert classify_response(200, body, "application/json") == (
        False,
        "access_denied",
    )


@pytest.mark.parametrize("content_type", ["application/json", "application/xml"])
def test_machine_readable_service_root_is_not_public_content(
    content_type: str,
) -> None:
    body = b'{"version":"1"}' if "json" in content_type else b"<status>ok</status>"

    assert classify_response(200, body, content_type) == (
        False,
        "service_endpoint",
    )


def test_machine_readable_access_denial_is_not_an_active_website() -> None:
    body = (
        b'{"status":"Failure","message":"forbidden: User '
        b'\\"system:anonymous\\" cannot get path \\"/\\""}'
    )

    assert classify_response(403, body) == (False, "access_denied")


def test_login_only_page_is_not_an_active_website() -> None:
    body = b'<html><title>Sign in</title><form><input type="password"></form></html>'

    assert classify_response(401, body) == (False, "login_only")


def test_public_content_with_a_login_control_remains_active() -> None:
    body = (
        b"<html><title>Learning portal</title><main><h1>Available courses</h1>"
        b"<p>Public course catalogue and student guidance.</p></main>"
        b'<form><input type="password"></form></html>'
    )

    assert classify_response(200, body) == (True, "public_response")


def test_short_visible_password_form_is_login_only() -> None:
    body = (
        b"<title>QLCB</title><form>Ten dang nhap: <input name='u'>Mat khau: <input name='p'></form>"
    )

    assert classify_response(200, body, "text/html") == (False, "login_only")


@pytest.mark.parametrize(
    "body",
    [
        b"<title>CFSSL</title><div id='cfssl'></div>",
        b"<html ng-app='esxUiApp'><body></body></html>",
        b"<title>BKOffice</title><div id='app'></div>",
        b"Invalid input format: email|pass|refresh_token|client_id",
        b"<title>D-Office-HUST</title><app-root>Please enable JavaScript</app-root>",
        "<title>BKSign | Hệ thống ký số</title><div id='root'></div>".encode(),
        b"<title>eDiploma</title><div id='root'></div>",
        b"<title>HUSTack</title><div id='root'></div>",
        b"<title>TSA-Exam | Phan mem thi</title><div id='root'></div>",
    ],
)
def test_service_shell_without_public_content_is_not_active(body: bytes) -> None:
    assert classify_response(200, body, "text/html") == (
        False,
        "service_endpoint",
    )


def test_probe_result_records_content_digest_for_duplicate_detection() -> None:
    result = ProbeResult("example.hust.edu.vn", True, "public_response")

    assert hasattr(result, "content_digest")


def test_html_access_denial_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<html><title>Access denied</title><h1>Forbidden</h1></html>",
        "text/html",
    ) == (False, "access_denied")


def test_registration_only_page_is_not_public_content() -> None:
    body = b"<title>Register Account</title><form>Create account</form>"

    assert classify_response(
        200,
        body,
        "text/html",
        "https://login.example.hust.edu.vn/register",
    ) == (False, "login_only")


def test_vietnamese_login_path_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<title>Portal</title><form><input type='password'></form>",
        "text/html",
        "https://example.hust.edu.vn/dang-nhap",
    ) == (False, "login_only")


@pytest.mark.parametrize("path", ["/weblogin.htm", "/changepass.php"])
def test_account_access_paths_are_not_public_content(path: str) -> None:
    assert classify_response(
        200,
        b"<title>Staff portal</title><form><input name='account'></form>",
        "text/html",
        f"https://example.hust.edu.vn{path}",
    ) == (False, "login_only")


def test_non_production_hostname_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<title>University</title><main><h1>Public news</h1></main>",
        "text/html",
        "https://hust-dev.hust.edu.vn/",
    ) == (False, "non_production")
