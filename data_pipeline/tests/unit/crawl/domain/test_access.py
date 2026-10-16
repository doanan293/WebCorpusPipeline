import time

import pytest

from data_pipeline.crawl.domain.access import (
    _parse_html,
    _visible_body_text,
    classify_access,
    classify_page_gate,
)


@pytest.mark.parametrize(
    ("status", "html", "outcome"),
    [
        (401, "<h1>Unauthorized</h1>", "access_denied"),
        (403, "<h1>Forbidden</h1>", "access_denied"),
        (200, '<form action="/login"><input type="password"></form>', "login_required"),
        (200, '<div class="g-recaptcha"></div>', "captcha_blocked"),
    ],
)
def test_access_classification_distinguishes_denial_from_gates(
    status: int, html: str, outcome: str
) -> None:
    decision = classify_access(status=status, url="https://example.com/", html=html, rendered=False)
    expected_playwright = outcome == "access_denied"
    assert (decision.outcome, decision.use_playwright) == (outcome, expected_playwright)


def test_rendered_http_denial_is_terminal() -> None:
    decision = classify_access(
        status=403,
        url="https://example.com/",
        html="<h1>Forbidden</h1>",
        rendered=True,
    )
    assert (decision.outcome, decision.reason, decision.use_playwright) == (
        "access_denied",
        "http_403",
        False,
    )


def test_public_shell_requests_browser_once() -> None:
    html = '<div id="app"></div><script src="/app.js"></script>'
    assert classify_access(
        status=200, url="https://example.com/", html=html, rendered=False
    ).use_playwright
    assert not classify_access(
        status=200, url="https://example.com/", html=html, rendered=True
    ).use_playwright


def test_public_shell_ignores_head_noscript_and_formatting_whitespace() -> None:
    html = """
        <html>
          <head>
            <title>Mạng lưới Doanh nghiệp và Cựu Sinh viên Đại học Bách khoa Hà Nội</title>
          </head>
          <body>
            <noscript>
              Your web browser must have JavaScript enabled for this application.
            </noscript>
            <div id="live-chat"></div>
            <script src="/js/index.js"></script>
          </body>
        </html>
    """

    decision = classify_access(
        status=200,
        url="https://connect.hust.edu.vn/",
        html=html,
        rendered=False,
    )

    assert (decision.outcome, decision.reason, decision.use_playwright) == (
        "public",
        "html_shell",
        True,
    )


def test_public_article_that_mentions_captcha_is_not_treated_as_a_challenge() -> None:
    html = "<article><h1>CAPTCHA accessibility research</h1><p>Public report.</p></article>"

    decision = classify_access(
        status=200,
        url="https://example.com/research",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_comment_form_captcha_does_not_block_public_article() -> None:
    html = """
        <div id="news-body"><h1>Public article</h1><p>Useful public content.</p></div>
        <div id="formcomment" class="comment-form">
          <form action="/vi/comment/post/">
            <img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">
            <input name="code">
          </form>
        </div>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_captcha_in_public_contact_form_does_not_block_page() -> None:
    html = """
        <main>
          <h1>Contact the faculty</h1>
          <p>Our address, telephone number, and office hours are public information.</p>
          <form action="/en/contact/">
            <label>Your message</label><textarea name="message"></textarea>
            <img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">
          </form>
        </main>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/en/contact/",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_captcha_flood_blocker_remains_an_access_gate() -> None:
    html = """
        <header>Faculty website navigation and other public chrome text.</header>
        <div class="floodblocker">
          <form id="formPassFlood" action="/">
            <p>Too many requests. Verify that you are human.</p>
            <div class="g-recaptcha"></div>
          </form>
        </div>
    """

    decision = classify_access(
        status=429,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "captcha_blocked"


def test_http_429_captcha_remains_an_access_gate_without_vendor_markup() -> None:
    html = """
        <header>Faculty website navigation and other public chrome text.</header>
        <form action="/">
          <p>Please verify that you are human.</p>
          <div class="g-recaptcha"></div>
        </form>
    """

    decision = classify_access(
        status=429,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "captcha_blocked"


def test_captcha_library_script_does_not_block_public_article() -> None:
    html = """
        <main><h1>Public article</h1><p>Useful public content.</p></main>
        <script src="https://captcha.example/api.js"></script>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


@pytest.mark.parametrize(
    "html,reason",
    [
        ("<main><h1>404</h1><p>Không tìm thấy nội dung</p></main>", "soft_404"),
        ('<form action="/login"><input type="password"></form>', "login"),
        ('<main><div class="g-recaptcha"></div></main>', "captcha"),
        ("<main></main>", "empty"),
    ],
)
def test_page_gate_blocks_noncontent_shells(html: str, reason: str) -> None:
    assert (
        classify_page_gate(status=200, url="https://a.test/p", html=html, text="").reason == reason
    )


def test_expansion_gate_uses_visible_body_text() -> None:
    body = " ".join(["Thông báo tuyển sinh năm học mới"] * 10)
    html = f'<body><div class="tin"><a href="/tin/1">{body}</a></div></body>'

    gate = classify_page_gate(status=200, url="https://a.test/", html=html, text="")

    assert (gate.expandable, gate.reason) == (True, None)


@pytest.mark.parametrize("html", ["", "   \n  ", "<!-- only a comment -->"])
def test_whitespace_html_is_empty_not_error(html: str) -> None:
    gate = classify_page_gate(status=200, url="https://a.test/p", html=html, text="")
    access = classify_access(status=200, url="https://a.test/p", html=html, rendered=False)

    assert gate.reason == "empty"
    assert access.outcome == "public"


def _comment_list_page(comments: int) -> str:
    """A WordPress-like page whose comment list holds many items, each followed by an HTML comment."""
    item = (
        '<li class="comment"><article><div class="flex-row"><div class="flex-col">'
        '<cite><a href="#">name {i}</a></cite> <span>says</span></div>'
        '<div class="comment-content"><p>spam text {i}</p></div></div></article></li>'
        "<!-- #comment-## -->"
    )
    return (
        "<html><body><main><p>Article body text here.</p><ol class='comment-list'>"
        + "".join(item.format(i=i) for i in range(comments))
        + "</ol></main></body></html>"
    )


def test_page_gate_stays_linear_on_a_huge_comment_list() -> None:
    # libxml2 evaluates `text()[not(ancestor::x)]` quadratically on this shape: 8000 comments
    # took ~9 s and the 22k-comment page that stalled the live crawl did not finish in hours.
    html = _comment_list_page(8000)

    started = time.monotonic()
    decision = classify_page_gate(
        status=200, url="https://a.test/post/", html=html, text="Article body text here."
    )

    assert time.monotonic() - started < 3
    assert decision.expandable


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><p>a <b>b</b> tail</p><script>x()</script>after<style>p{}</style></body></html>",
        "<html><body><noscript>no</noscript><template><p>t</p></template> kept <!-- c --> tail</body></html>",
        "<div>no body <span>inner</span> out</div>",
        "<html><body><form><p>in form</p></form> outside <!--x--><i>i</i></body></html>",
        _comment_list_page(3),
        "",
        "   ",
    ],
)
def test_visible_text_matches_the_xpath_definition(html: str) -> None:
    root = _parse_html(html)
    expected = ""
    if root is not None:
        bodies = root.xpath("//body")
        container = bodies[0] if bodies else root
        values = container.xpath(
            ".//text()[not(ancestor::script) and not(ancestor::style) "
            "and not(ancestor::noscript) and not(ancestor::template)]"
        )
        expected = " ".join(" ".join(str(v).split()) for v in values if str(v).strip())

    assert _visible_body_text(html) == expected
