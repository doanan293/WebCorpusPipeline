from typing import Any

from lxml import html as lxml_html

from data_pipeline.crawl.extract.article import analyze_article
from data_pipeline.crawl.extract.content import select_content
from data_pipeline.crawl.extract.links import HtmlDiscovery


def extract_html(html: bytes | str, url: str, encoding: str | None = None) -> dict[str, Any]:
    return analyze_article(html, url, encoding).article


def discover_html(html: bytes | str, url: str, encoding: str | None = None) -> list[HtmlDiscovery]:
    return analyze_article(html, url, encoding).discoveries


DECLARED_UTF8_HTML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<html><head><title>Thông báo</title></head>"
    "<body><main><p>Nội dung tiếng Việt.</p></main>"
    '<a href="/tin-moi">Tin mới</a></body></html>'
).encode()


def test_extract_html_accepts_bytes_with_xml_encoding_declaration() -> None:
    result = extract_html(DECLARED_UTF8_HTML, "https://site.test/article", "utf-8")
    assert result["title"] == "Thông báo"
    assert result["text"] == "Nội dung tiếng Việt."


def test_discover_html_accepts_bytes_with_xml_encoding_declaration() -> None:
    assert discover_html(DECLARED_UTF8_HTML, "https://site.test/article", "utf-8") == [
        {"url": "https://site.test/tin-moi", "text": "Tin mới", "kind": "html", "rel": []}
    ]


def test_article_and_outgoing_links() -> None:
    result = extract_html(
        "<nav>Menu</nav><article><h1>Tin mới</h1><p>Nội dung.</p></article>"
        '<a href="/b"> B </a><a href="https://outside.test/c">C</a>',
        "https://site.test/a",
    )

    assert result["title"] == "Tin mới"
    assert "Nội dung." in result["text"] and "Menu" not in result["text"]
    assert result["links"] == [
        {"url": "https://site.test/b", "text": "B"},
        {"url": "https://outside.test/c", "text": "C"},
    ]


def test_html_base_image_labels_and_duplicate_href_labels() -> None:
    html = """
        <html><head><base href="https://cdn.site.test/news/"><title>  Site story </title></head>
        <body><main><p>First paragraph.</p><p>Second paragraph.</p></main>
        <a href="item?view=full#comments"><img alt="Photo story"></a>
        <a href="item?view=full">Read story</a>
        <a href="item?view=full">Read story</a>
        <a href="/labelled" aria-label="Accessible label"></a>
        <a href="/titled" title="Title fallback"></a></body></html>
    """

    result = extract_html(html, "https://site.test/original")

    assert result["title"] == "Site story"
    assert result["text"] == "First paragraph.\nSecond paragraph."
    assert result["links"] == [
        {"url": "https://cdn.site.test/news/item?view=full", "text": "Photo story"},
        {"url": "https://cdn.site.test/news/item?view=full", "text": "Read story"},
        {"url": "https://cdn.site.test/labelled", "text": "Accessible label"},
        {"url": "https://cdn.site.test/titled", "text": "Title fallback"},
    ]
    assert result["warnings"] == []


def test_malformed_html_preserves_paragraph_boundaries() -> None:
    result = extract_html(
        "<main><p>Paragraph one.<!-- editorial note --><p>Paragraph two."
        "<aside>Related links</aside></main>",
        "https://site.test/page",
    )

    assert result["text"] == "Paragraph one.\nParagraph two."


def test_missing_title_and_text_emit_warnings() -> None:
    result = extract_html(
        "<html><head><title> </title></head><body><nav>Menu</nav><script>noise()</script></body></html>",
        "https://site.test/empty",
    )

    assert result["title"] == ""
    assert result["text"] == ""
    assert result["links"] == []
    assert result["warnings"] == ["missing_title", "missing_text"]


def test_discovery_includes_media_and_srcset_but_discards_transients() -> None:
    html = """
        <base href="https://assets.site.test/v1/">
        <a href="/article?id=1">Article</a>
        <a href="/article?id=2">Article</a>
        <img src="hero.jpg?size=large" alt="Hero"
             srcset="hero-small.jpg 1x, hero-large.webp?crop=wide 2x">
        <img src="/index.php?second=cronjobs&amp;p=random" alt="cron">
        <video src="movie.mp4" poster="poster.png"></video>
        <source src="movie.webm" srcset="still-480.jpg 480w, still-960.jpg 960w">
        <script src="bundle"></script>
        <link rel="stylesheet" href="styles">
        <link rel="preload" as="font" href="font-file">
    """

    assert discover_html(html, "https://site.test/page") == [
        {
            "url": "https://assets.site.test/article?id=1",
            "text": "Article",
            "kind": "html",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/article?id=2",
            "text": "Article",
            "kind": "html",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/v1/hero.jpg?size=large",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/v1/hero-small.jpg",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/v1/hero-large.webp?crop=wide",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/index.php?second=cronjobs&p=random",
            "text": "cron",
            "kind": "file",
            "rel": [],
        },
        {"url": "https://assets.site.test/v1/movie.mp4", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/poster.png", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/movie.webm", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/still-480.jpg", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/still-960.jpg", "text": "", "kind": "file", "rel": []},
    ]


def test_title_text_and_links_fields_are_present() -> None:

    article = analyze_article(
        "<main><h1>A</h1><p>B</p><a href='/c'>C</a></main>", "https://a.test/"
    ).article
    assert article["title"] == "A"
    assert article["text"] == "A\nB\nC"
    assert article["links"] == [{"url": "https://a.test/c", "text": "C"}]


def test_select_content_falls_back_to_body_for_tiny_candidate() -> None:
    root = lxml_html.fromstring(
        "<html><body>"
        '<ul class="content-icons"><li><a href="/fb">f</a></li><li>in</li></ul>'
        "<div><p>Sinh viên cần nộp hồ sơ trước ngày 15 tháng 10 tại phòng đào tạo.</p></div>"
        "</body></html>"
    )

    assert select_content(root).tag == "body"
