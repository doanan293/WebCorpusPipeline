from __future__ import annotations

import asyncio

from crawler.application.downloader.fetch import fetch_and_forward
from crawler.domain.downloader.models import Fetched
from tests.fakes.downloader import FakeGetter, FakeReaderClient, FakeRenderer

ARTICLE = (
    b"<html><head><title>T</title></head><body><article><p>"
    + b"Noi dung bai viet. " * 20
    + b"</p></article></body></html>"
)
LOGIN = (
    b"<html><body><form action='/login' method='post'>"
    b"<input name='u'><input type='password' name='p'></form></body></html>"
)
SHELL = (
    b"<html><head><script src='/app.js'></script></head><body><div id='root'></div></body></html>"
)


def _page(
    url: str, body: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200
) -> Fetched:
    return Fetched(final_url=url, status=status, content_type=content_type, body=body)


def _run(
    pages: dict[str, Fetched], url: str, renderer: FakeRenderer | None = None, ok: bool = True
):
    reader = FakeReaderClient(ok)
    renderer = renderer or FakeRenderer()
    outcome = asyncio.run(fetch_and_forward(url, FakeGetter(pages), renderer, reader))
    return outcome, reader, renderer


def test_article_forwarded_as_html() -> None:
    outcome, reader, renderer = _run({"https://h/a": _page("https://h/a", ARTICLE)}, "https://h/a")
    assert outcome == "forwarded"
    assert [(page.final_url, fmt) for page, fmt in reader.sent] == [("https://h/a", "html")]
    assert renderer.rendered == []


def test_login_page_is_blocked() -> None:
    outcome, reader, _ = _run({"https://h/a": _page("https://h/a", LOGIN)}, "https://h/a")
    assert outcome == "blocked"
    assert reader.sent == []


def test_js_shell_is_rendered() -> None:
    rendered = _page("https://h/a", ARTICLE)
    outcome, reader, renderer = _run(
        {"https://h/a": _page("https://h/a", SHELL)},
        "https://h/a",
        FakeRenderer({"https://h/a": rendered}),
    )
    assert outcome == "forwarded"
    assert renderer.rendered == ["https://h/a"]
    assert reader.sent[0][0].body == ARTICLE


def test_http_error_is_failed() -> None:
    outcome, reader, _ = _run(
        {"https://h/a": _page("https://h/a", b"no", status=404)}, "https://h/a"
    )
    assert outcome == "failed"
    assert reader.sent == []


def test_unreachable_is_failed() -> None:
    assert _run({}, "https://h/a")[0] == "failed"


def test_document_forwarded_as_other() -> None:
    pdf = _page("https://h/a.pdf", b"%PDF-1.4", "application/pdf")
    outcome, reader, _ = _run({"https://h/a.pdf": pdf}, "https://h/a.pdf")
    assert outcome == "forwarded"
    assert reader.sent[0][1] == "other"


def test_image_is_skipped() -> None:
    image = _page("https://h/a.png", b"\x89PNG", "image/png")
    assert _run({"https://h/a.png": image}, "https://h/a.png")[0] == "skipped"


def test_reader_down_is_unsent() -> None:
    assert (
        _run({"https://h/a": _page("https://h/a", ARTICLE)}, "https://h/a", ok=False)[0] == "unsent"
    )
