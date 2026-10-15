import pytest

from data_pipeline.crawl.extract.article import analyze_article
from data_pipeline.crawl.extract.dates import parse_page_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020-02-23+0723:09:00", "2020-02-23T23:09:00+07:00"),
        ("2026-09-08T08:00:00+07:00", "2026-09-08T08:00:00+07:00"),
        ("2026-09-08T01:00:00Z", "2026-09-08T01:00:00+00:00"),
        ("2024-05-16 10:00:00", "2024-05-16T10:00:00+07:00"),
        ("2024-05-16", "2024-05-16T00:00:00+07:00"),
        ("hôm qua", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_page_date(raw: str | None, expected: str | None) -> None:
    assert parse_page_date(raw) == expected


def test_metadata_stores_iso_dates_and_warns_on_unparsed() -> None:
    html = (
        '<html><head><meta property="article:published_time" content="hôm qua">'
        '<meta property="article:modified_time" content="2020-02-23+0723:09:00"></head>'
        "<body><main><h1>Tin</h1><p>Nội dung bài viết.</p></main></body></html>"
    )

    article = analyze_article(html.encode(), "https://a.test/tin", "utf-8").article

    assert article["published_at"] is None
    assert article["modified_at"] == "2020-02-23T23:09:00+07:00"
    assert "unparsed_date:published_at" in article["warnings"]
