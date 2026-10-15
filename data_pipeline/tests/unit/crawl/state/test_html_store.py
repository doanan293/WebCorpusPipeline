import gzip
from pathlib import Path

from scrapy.http import Request

from data_pipeline.crawl.output.writer import CrawlWriter
from data_pipeline.crawl.state.html_store import HtmlStore, RawPage
from tests.unit.crawl.spider.test_refresh import make_spider, open_writer, page


def raw_page_urls(writer: CrawlWriter) -> list[str]:
    rows = writer.state.connection.execute("SELECT url FROM raw_pages ORDER BY url;")
    return [str(row[0]) for row in rows]


def storing_writer(tmp_path: Path) -> CrawlWriter:
    plain = open_writer(tmp_path)
    store = HtmlStore(tmp_path / "html", plain.state.connection)
    return CrawlWriter(tmp_path, plain.state, plain.options, html_store=store)


def put(store: HtmlStore, url: str, body: bytes) -> str:
    return store.put(
        url,
        body,
        final_url=url,
        status=200,
        content_type="text/html; charset=utf-8",
        fetched_at="2026-10-15T00:00:00+00:00",
    )


def test_put_dedups_identical_bodies(tmp_path: Path) -> None:
    writer = storing_writer(tmp_path)
    store = writer.html_store
    assert store is not None

    first = put(store, "https://a.test/1", b"<p>same</p>")
    second = put(store, "https://a.test/2", b"<p>same</p>")

    assert first == second
    files = list((tmp_path / "html").rglob("*.html.gz"))
    assert [path.name for path in files] == [f"{first}.html.gz"]
    assert gzip.decompress(files[0].read_bytes()) == b"<p>same</p>"
    assert raw_page_urls(writer) == ["https://a.test/1", "https://a.test/2"]
    writer.close()


def test_get_roundtrips_body_and_metadata(tmp_path: Path) -> None:
    writer = storing_writer(tmp_path)
    store = writer.html_store
    assert store is not None
    put(store, "https://a.test/1", "<p>Tiếng Việt</p>".encode())

    assert store.get("https://a.test/1") == RawPage(
        url="https://a.test/1",
        final_url="https://a.test/1",
        status=200,
        content_type="text/html; charset=utf-8",
        fetched_at="2026-10-15T00:00:00+00:00",
        body="<p>Tiếng Việt</p>".encode(),
    )
    assert store.get("https://a.test/missing") is None
    writer.close()


ARTICLE = "<main><h1>Tin</h1><p>Nội dung bài viết của khoa, đủ dài để trích xuất.</p></main>"


def page_request(url: str) -> Request:
    return Request(
        url,
        meta={
            "input_url": url,
            "seed_mode": "recursive",
            "response_purpose": "page",
            "discovery_source": "html_link",
        },
    )


def crawl_pages(writer: CrawlWriter) -> None:
    spider = make_spider(writer)
    for url, body, status in (
        ("https://a.test/1", ARTICLE, 200),
        ("https://a.test/1-copy", ARTICLE, 200),
        ("https://a.test/broken", "<h1>Server error</h1>", 500),
    ):
        list(spider.parse_response(page(page_request(url), body, status=status)))


def test_html_spider_stores_every_extracted_response(tmp_path: Path) -> None:
    writer = storing_writer(tmp_path)

    crawl_pages(writer)

    assert raw_page_urls(writer) == ["https://a.test/1", "https://a.test/1-copy"]
    writer.close()


def test_store_html_false_writes_nothing(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)

    crawl_pages(writer)

    assert raw_page_urls(writer) == []
    assert not (tmp_path / "html").exists()
    writer.close()
