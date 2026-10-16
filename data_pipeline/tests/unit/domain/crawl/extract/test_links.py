from typing import Any

import pytest
from lxml import html as lxml_html

from data_pipeline.domain.crawl.extract import links
from data_pipeline.domain.crawl.extract.links import (
    discoveries_from_root,
    normalize_url,
    resource_kind,
)

DECLARED_UTF8_HTML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<html><head><title>Thông báo</title></head>"
    "<body><main><p>Nội dung tiếng Việt.</p></main>"
    '<a href="/tin-moi">Tin mới</a></body></html>'
).encode()


def test_query_and_encoding_survive() -> None:
    assert (
        normalize_url("/a%2Fb?page=2#part", "https://site.test/x")
        == "https://site.test/a%2Fb?page=2"
    )
    assert (
        normalize_url(
            "/find?q=a%2Fb&tag=one&tag=two&utm_source=newsletter&empty=",
            "https://site.test/x",
        )
        == "https://site.test/find?q=a%2Fb&tag=one&tag=two&empty="
    )


@pytest.mark.parametrize(
    "value",
    [
        "mailto:editor@site.test",
        "javascript:void(0)",
        "https://user:secret@site.test/private",
        "https://site.test:invalid/page",
        "//:443/missing-host",
    ],
)
def test_normalize_url_rejects_unsafe_or_malformed_values(value: str) -> None:
    assert normalize_url(value, "https://site.test/") is None


@pytest.mark.parametrize(
    ("url", "content_type", "expected"),
    [
        ("https://site.test/story", "", "html"),
        ("https://site.test/index.HTML?view=1", "", "html"),
        ("https://site.test/report.pdf", "", "file"),
        ("https://site.test/photo.JPG", "", "file"),
        ("https://site.test/movie.mp4", "", "file"),
        ("https://site.test/app.js", "", "transient"),
        ("https://site.test/font.woff2", "", "transient"),
        ("https://site.test/report.pdf", "text/html; charset=utf-8", "html"),
        ("https://site.test/download", "application/pdf", "file"),
        ("https://site.test/index.html", "image/jpeg", "file"),
        ("https://site.test/photo.jpg", "text/css", "transient"),
        ("https://site.test/font", "application/font-woff", "transient"),
        ("https://site.test/archive.unrecognized", "", "unknown"),
    ],
)
def test_resource_kind_uses_mime_before_extension(
    url: str, content_type: str, expected: str
) -> None:
    assert resource_kind(url, content_type) == expected


def test_observed_refs_skip_link_noise() -> None:
    root = lxml_html.fromstring(
        "<html><head>"
        '<link rel="icon" href="/favicon.ico"><link rel="manifest" href="/site.webmanifest">'
        '<link rel="preconnect" href="https://fonts.test"><link rel="next" href="/news?page=2">'
        '<link rel="alternate" hreflang="en" href="/en/news"><link rel="alternate" href="/feed">'
        '</head><body><a href="/a">A</a></body></html>'
    )

    urls = [item["url"] for item in discoveries_from_root(root, "https://a.test/news")]

    assert urls == ["https://a.test/news?page=2", "https://a.test/en/news", "https://a.test/a"]


def test_labels_computed_only_for_href_src(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original = links._element_label

    def spy(element: Any) -> str:
        calls.append(element.tag)
        return original(element)

    monkeypatch.setattr(links, "_element_label", spy)
    root = lxml_html.fromstring(
        '<html><body><div><p>Text <b>bold</b></p><a href="/a">A</a>'
        '<img src="/i.png"><span>x</span></div></body></html>'
    )

    discoveries_from_root(root, "https://a.test/")

    assert sorted(calls) == ["a", "img"]
